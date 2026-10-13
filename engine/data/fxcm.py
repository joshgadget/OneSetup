"""FXCM adapter: forex and gold, per the official *FXCM Socket REST API User Guide*
v2.1.1 (https://fxcm-rest.readthedocs.io/en/latest/socketrestapispecs.html).

Auth (as documented): generate a persistent Access Token in Trading Station Web
(https://tradingstation.fxcm.com/). Open a socket.io (Engine.IO v3) polling session:

    GET /socket.io/?access_token=<token>&EIO=3&transport=polling&b64=1

and use the returned socket id on every request:

    Authorization: Bearer <socket_id><access_token>

Endpoints used:
    GET  /trading/get_instruments/                 -> available symbols
    GET  /trading/get_model/?models=Offer          -> instrument metadata + offer ids
    GET  /candles/{offer_id}/{period_id}?num=N     -> [ts, BidO, BidC, BidH, BidL, AskO, AskC, AskH, AskL, Qty]
    POST /subscribe  pairs=SYMBOL                  -> live updates over the socket

Notes recorded in DECISIONS.md: candle arrays are Open, Close, High, Low per side
(not OHLC); duplicate timestamps are possible and are collapsed; the unfinished
candle is dropped by default.

[BLOCKED 2026-10-12] The hosts api.fxcm.com and api-demo.fxcm.com no longer exist
in DNS (authoritative NXDOMAIN, verified via DNS-over-HTTPS), so the FXCM Socket
REST API appears retired. This adapter is kept, correct against the published spec,
but it cannot fetch data until FXCM provides a working endpoint. See DECISIONS.md
D-006 for the replacement options (ForexConnect, FCLite, MT5 bridge, or OANDA).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Iterable

import httpx

from engine.data.base import DataAdapter, DataAdapterError
from engine.models import (
    Candle,
    InstrumentMetadata,
    MarketClass,
    SymbolInfo,
    Tick,
    Timeframe,
    utcnow,
)

log = logging.getLogger(__name__)

# Documented period ids (lowercase accepted; the doc's own sample uses /candles/1/h1/)
_PERIOD_IDS: dict[Timeframe, str] = {
    Timeframe.M1: "m1", Timeframe.M5: "m5", Timeframe.M15: "m15", Timeframe.M30: "m30",
    Timeframe.H1: "h1", Timeframe.H2: "h2", Timeframe.H3: "h3", Timeframe.H4: "h4",
    Timeframe.H6: "h6", Timeframe.H8: "h8",
    Timeframe.D1: "d1", Timeframe.W1: "w1", Timeframe.MN1: "m1x",
}
_DOCUMENTED_MAX_CANDLES = 10_000


def compact(symbol: str) -> str:
    """Canonical id for FXCM symbols: 'GBP/USD' -> 'GBPUSD'."""
    return symbol.replace("/", "").strip().upper()


def _market_class(provider_symbol: str) -> MarketClass:
    base = provider_symbol.split("/")[0].upper()
    return MarketClass.GOLD if base in {"XAU", "XAG"} else MarketClass.FOREX


def _ci_get(row: dict[str, Any], *names: str, default: Any = None) -> Any:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in lowered and lowered[name.lower()] not in (None, ""):
            return lowered[name.lower()]
    return default


def _parse_eio_payload(text: str) -> dict[str, Any] | None:
    """Engine.IO v3 polling bodies look like '<len>:0{json}'."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


class FXCMAdapter(DataAdapter):
    name = "fxcm"

    def __init__(
        self,
        access_token: str,
        host: str = "api-demo.fxcm.com",
        price_side: str = "mid",
        *,
        session_ttl_seconds: float = 30.0,
        min_request_interval: float = 0.25,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not access_token:
            raise DataAdapterError(
                "FXCM_ACCESS_TOKEN is empty. Note: FXCM's REST hosts (api.fxcm.com, "
                "api-demo.fxcm.com) no longer resolve in DNS; see DECISIONS.md D-006 "
                "before generating a token."
            )
        if price_side not in {"mid", "bid", "ask"}:
            raise DataAdapterError(f"FXCM_PRICE_SIDE must be mid|bid|ask, got {price_side!r}")
        self.access_token = access_token.strip()
        self.host = host.strip()
        self.price_side = price_side
        self.base = f"https://{self.host}"
        self._client = client or httpx.AsyncClient(timeout=25.0, headers={"User-Agent": "trading-analysis/0.1"})
        self._owns_client = client is None
        self._socket_id: str | None = None
        self._handshake_at: float = 0.0
        self._session_ttl = session_ttl_seconds
        self._lock = asyncio.Lock()
        self._last_request_at: float = 0.0
        self._min_interval = min_request_interval
        self._offers: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ session

    async def _handshake(self) -> str:
        url = f"{self.base}/socket.io/"
        params = {"access_token": self.access_token, "EIO": "3", "transport": "polling", "b64": "1"}
        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise DataAdapterError(
                f"FXCM socket handshake failed: cannot reach {self.host}. FXCM's REST hosts "
                f"api.fxcm.com and api-demo.fxcm.com returned NXDOMAIN when checked on 2026-10-12; "
                f"see DECISIONS.md D-006 for the currently supported FXCM options. ({exc})"
            ) from exc
        except httpx.HTTPError as exc:
            raise DataAdapterError(f"FXCM socket handshake failed: {exc}") from exc
        payload = _parse_eio_payload(resp.text)
        sid = (payload or {}).get("sid")
        if not sid:
            raise DataAdapterError(
                "FXCM socket handshake returned no sid; check FXCM_ACCESS_TOKEN and FXCM_HOST "
                f"(body starts: {resp.text[:120]!r})"
            )
        self._socket_id = sid
        self._handshake_at = time.monotonic()
        return sid

    async def _ensure_session(self, *, force: bool = False) -> str:
        if force or self._socket_id is None or (time.monotonic() - self._handshake_at) > self._session_ttl:
            return await self._handshake()
        return self._socket_id

    # ------------------------------------------------------------------ requests

    async def _request(self, method: str, path: str, *, params: dict | None = None, data: dict | None = None, _retry: bool = True) -> dict[str, Any]:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            sid = await self._ensure_session()
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Bearer {sid}{self.access_token}",
            }
        url = f"{self.base}{path}"
        try:
            resp = await self._client.request(method, url, params=params, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise DataAdapterError(f"FXCM {method} {path} failed: {exc}") from exc
        finally:
            self._last_request_at = time.monotonic()

        if resp.status_code in (401, 403) and _retry:
            await self._ensure_session(force=True)
            return await self._request(method, path, params=params, data=data, _retry=False)
        if resp.status_code >= 500 and _retry:
            await asyncio.sleep(1.0)
            return await self._request(method, path, params=params, data=data, _retry=False)
        if resp.status_code >= 400:
            raise DataAdapterError(f"FXCM {method} {path} returned HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise DataAdapterError(f"FXCM {method} {path} returned non-JSON: {resp.text[:200]!r}") from exc
        envelope = payload.get("response") if isinstance(payload, dict) else None
        if isinstance(envelope, dict) and envelope.get("executed") is False:
            error = envelope.get("error") or "unknown error"
            if _retry:
                await self._ensure_session(force=True)
                return await self._request(method, path, params=params, data=data, _retry=False)
            raise DataAdapterError(f"FXCM {method} {path} not executed: {error}")
        return payload

    async def raw(self, method: str, path: str, *, params: dict | None = None, data: dict | None = None) -> dict[str, Any]:
        """Diagnostic passthrough for scripts/smoke_data.py. Never used by detectors."""
        return await self._request(method, path, params=params, data=data)

    # ------------------------------------------------------------------ symbols

    async def list_symbols(self) -> list[SymbolInfo]:
        payload = await self._request("GET", "/trading/get_instruments/")
        instruments = (payload.get("data") or {}).get("instrument") or []
        out: list[SymbolInfo] = []
        for row in instruments:
            provider_symbol = str(row.get("symbol") or "").strip()
            if not provider_symbol or row.get("visible") is False:
                continue
            canonical = compact(provider_symbol)
            out.append(
                SymbolInfo(
                    canonical_id=canonical,
                    display=provider_symbol,
                    market_class=_market_class(provider_symbol),
                    venue="FXCM",
                    provider_symbols={"fxcm": provider_symbol},
                )
            )
        out.sort(key=lambda s: s.canonical_id)
        return out

    async def _offer_map(self, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
        if self._offers and not refresh:
            return self._offers
        payload = await self._request("GET", "/trading/get_model/", params={"models": "Offer"})
        rows: Iterable[Any] | None = None
        containers: list[dict[str, Any]] = [payload]
        if isinstance(payload.get("data"), dict):
            containers.append(payload["data"])
        for container in containers:
            for key, value in container.items():
                if isinstance(key, str) and key.lower() in {"offers", "offer"} and isinstance(value, list):
                    rows = value
                    break
            if rows is not None:
                break
        if rows is None:
            raise DataAdapterError(
                "FXCM get_model did not return an Offers table; keys="
                + ", ".join(sorted(str(k) for k in payload.keys()))
            )
        offers: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(_ci_get(row, "Symbol", "symbol", default="") or "").strip()
            if symbol:
                offers[compact(symbol)] = row
        if not offers:
            raise DataAdapterError("FXCM Offers table was empty")
        self._offers = offers
        return offers

    async def get_instrument_metadata(self, canonical_id: str) -> InstrumentMetadata:
        offers = await self._offer_map()
        row = offers.get(compact(canonical_id))
        if row is None:
            offers = await self._offer_map(refresh=True)
            row = offers.get(compact(canonical_id))
        if row is None:
            raise DataAdapterError(f"FXCM does not list instrument {canonical_id!r}")
        provider_symbol = str(_ci_get(row, "Symbol", "symbol", default=canonical_id))
        digits = _ci_get(row, "fractionDigits", "Digits", "ratePrecision", default=None)
        tick_size = _ci_get(row, "pip", "pipSize", default=None)
        if tick_size is None and digits is not None:
            try:
                tick_size = 10 ** (-int(digits))
            except (TypeError, ValueError):
                tick_size = None
        bid = _ci_get(row, "Bid", "bid", default=None)
        ask = _ci_get(row, "Ask", "ask", default=None)
        spread = None
        try:
            if bid is not None and ask is not None:
                spread = float(ask) - float(bid)
        except (TypeError, ValueError):
            spread = None
        return InstrumentMetadata(
            canonical_id=compact(provider_symbol),
            symbol=provider_symbol,
            market_class=_market_class(provider_symbol),
            venue="FXCM",
            quote_currency=str(_ci_get(row, "QuoteCurrency", default=provider_symbol.split("/")[-1] if "/" in provider_symbol else "")),
            base_currency=str(_ci_get(row, "BaseCurrency", default=provider_symbol.split("/")[0] if "/" in provider_symbol else "")),
            digits=int(digits) if digits is not None else None,
            tick_size=float(tick_size) if tick_size is not None else None,
            contract_size=float(_ci_get(row, "ContractMultiplier", "contractMultiplier", default=None) or 0) or None,
            # FXCM's REST Offers table does not publish a minimum stop distance; [OPEN]
            min_stop_distance=None,
            typical_spread=spread,
            price_grid=None,
            trading_hours=None,
            value_per_price_unit=None,
            extra={"raw_offer": {k: v for k, v in row.items()}},
        )

    # ------------------------------------------------------------------ candles

    async def _offer_id(self, canonical_id: str) -> tuple[str, str]:
        offers = await self._offer_map()
        row = offers.get(compact(canonical_id))
        if row is None:
            offers = await self._offer_map(refresh=True)
            row = offers.get(compact(canonical_id))
        if row is None:
            raise DataAdapterError(f"FXCM does not list instrument {canonical_id!r}")
        offer_id = _ci_get(row, "OfferId", "offerId", default=None)
        if offer_id is None:
            raise DataAdapterError(f"FXCM offer row for {canonical_id!r} has no OfferId")
        return str(offer_id), str(_ci_get(row, "Symbol", default=canonical_id))

    async def get_candles(
        self,
        canonical_id: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        closed_only: bool = True,
    ) -> list[Candle]:
        offer_id, provider_symbol = await self._offer_id(canonical_id)
        period = _PERIOD_IDS[timeframe]
        num = int(limit) if limit else _DOCUMENTED_MAX_CANDLES
        num = max(1, min(num, _DOCUMENTED_MAX_CANDLES))
        params: dict[str, Any] = {"num": num}
        if start is not None:
            params["from"] = int(start.timestamp())
        if end is not None:
            params["to"] = int(end.timestamp())
        payload = await self._request("GET", f"/candles/{offer_id}/{period}/", params=params)
        rows = payload.get("candles") or []
        candles = self._parse_candles(rows, canonical_id=compact(provider_symbol), timeframe=timeframe)
        if closed_only:
            candles = [c for c in candles if c.is_closed]
        return candles

    def _parse_candles(self, rows: list[Any], *, canonical_id: str, timeframe: Timeframe) -> list[Candle]:
        by_ts: dict[int, list[float]] = {}
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 10:
                log.warning("FXCM candle row with unexpected shape skipped: %r", row)
                continue
            try:
                ts = int(row[0])
                values = [float(v) for v in row[1:10]]
            except (TypeError, ValueError):
                log.warning("FXCM candle row with non-numeric values skipped: %r", row)
                continue
            by_ts[ts] = values  # duplicate timestamps: keep the last, as documented samples show repeats
        now = utcnow()
        out: list[Candle] = []
        for ts in sorted(by_ts):
            bid_o, bid_c, bid_h, bid_l, ask_o, ask_c, ask_h, ask_l = by_ts[ts][:8]
            qty = by_ts[ts][8] if len(by_ts[ts]) > 8 else None
            if self.price_side == "bid":
                o, h, l, c = bid_o, bid_h, bid_l, bid_c
            elif self.price_side == "ask":
                o, h, l, c = ask_o, ask_h, ask_l, ask_c
            else:
                o = (bid_o + ask_o) / 2
                h = (bid_h + ask_h) / 2
                l = (bid_l + ask_l) / 2
                c = (bid_c + ask_c) / 2
            open_time = datetime.fromtimestamp(ts, tz=timezone.utc)
            is_closed = (open_time + timedelta(seconds=timeframe.seconds)) <= now
            out.append(
                Candle(
                    symbol=canonical_id,
                    timeframe=timeframe,
                    open_time=open_time,
                    open=o, high=h, low=l, close=c,
                    tick_volume=qty,
                    real_volume=None,
                    is_closed=is_closed,
                    source=f"fxcm:{self.price_side}",
                )
            )
        return out

    # ------------------------------------------------------------------ streaming

    async def subscribe(self, canonical_id: str) -> AsyncIterator[Tick]:  # type: ignore[override]
        """Live prices via the documented socket.io polling channel.

        Implemented from the published frame format; verified by smoke test with a
        real access token. REST polling is used rather than a websocket upgrade so
        the adapter stays dependency-light.
        """
        _, provider_symbol = await self._offer_id(canonical_id)
        await self._request("POST", "/subscribe", data={"pairs": provider_symbol})
        while True:
            sid = await self._ensure_session()
            url = f"{self.base}/socket.io/"
            params = {"EIO": "3", "transport": "polling", "sid": sid, "t": str(int(time.time() * 1000))}
            try:
                resp = await self._client.get(url, params=params, timeout=60.0)
            except httpx.HTTPError as exc:
                raise DataAdapterError(f"FXCM socket poll failed: {exc}") from exc
            if resp.status_code in (400, 401, 403):
                await self._ensure_session(force=True)
                continue
            for symbol, tick in _iter_price_events(resp.text):
                if compact(symbol) == compact(canonical_id):
                    yield tick

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _extract_json_array(text: str, start: int) -> tuple[str, int] | None:
    """Scan a JSON array starting at text[start] == '[', respecting strings and nesting."""
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1], i + 1
    return None


def _iter_price_events(body: str) -> Iterable[tuple[str, Tick]]:
    """Extract socket.io price events: 42["EUR/USD", "{\"Updated\":..,\"Rates\":[bid,ask,high,low]}"]."""
    idx = 0
    while True:
        start = body.find("42[", idx)
        if start < 0:
            return
        found = _extract_json_array(body, start + 2)
        if found is None:
            return
        raw, idx = found
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(args, list) or len(args) < 2 or not isinstance(args[1], str):
            continue
        symbol = str(args[0])
        try:
            payload = json.loads(args[1])
            rates = payload.get("Rates") or []
            updated = payload.get("Updated")
            bid, ask = float(rates[0]), float(rates[1])
            ts = datetime.fromtimestamp(int(updated), tz=timezone.utc)
        except (ValueError, TypeError, IndexError, json.JSONDecodeError):
            continue
        yield symbol, Tick(symbol=compact(symbol), time=ts, bid=bid, ask=ask)