"""Crypto futures and perpetuals via CCXT public market data.

No API key is needed for candles and funding history: these are public endpoints.
Version 1 never places orders, so no trading key is accepted by this adapter.
Funding and mark-price data are exposed for the markets checks in the spec
(funding cost and liquidation guard, Phase 6+).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from engine.data.base import DataAdapter, DataAdapterError
from engine.data.resample import can_resample, resample_candles
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

_CCXT_TF: dict[Timeframe, str] = {
    Timeframe.M1: "1m", Timeframe.M5: "5m", Timeframe.M15: "15m", Timeframe.M30: "30m",
    Timeframe.H1: "1h", Timeframe.H2: "2h", Timeframe.H3: "3h", Timeframe.H4: "4h",
    Timeframe.H6: "6h", Timeframe.H8: "8h", Timeframe.D1: "1d", Timeframe.W1: "1w",
}
_MAX_FETCH = 1000
_DOCUMENTED_MAX = 10000  # venue-side cap on candles per request, kept generous


class CCXTAdapter(DataAdapter):
    name = "ccxt"

    def __init__(self, exchange: str = "bybit", default_type: str = "swap") -> None:
        self.exchange_id = exchange
        self.default_type = default_type
        self._exchange: Any | None = None
        self._markets: dict[str, Any] | None = None
        self._loop: Any | None = None

    # ------------------------------------------------------------------ infra

    async def _ex(self) -> Any:
        loop = asyncio.get_running_loop()
        if self._exchange is not None and self._loop is not loop:
            # A ccxt client belongs to the loop that built it: its rate-limit throttler
            # schedules tasks on that loop, so reusing it from another loop raises
            # "Event loop is closed". Drop it and build one for the loop in use.
            self._exchange = None
            self._markets = None
        if self._exchange is None:
            try:
                import ccxt.async_support as ccxt_async
            except ImportError as exc:  # pragma: no cover
                raise DataAdapterError("ccxt is not installed") from exc
            if not hasattr(ccxt_async, self.exchange_id):
                raise DataAdapterError(f"ccxt has no exchange {self.exchange_id!r}")
            klass = getattr(ccxt_async, self.exchange_id)
            self._exchange = klass({"enableRateLimit": True, "options": {"defaultType": self.default_type}})
            self._loop = loop
        return self._exchange

    async def _load_markets(self) -> dict[str, Any]:
        if self._markets is None:
            ex = await self._ex()
            self._markets = await ex.load_markets()
        return self._markets

    def _market(self, canonical_id: str) -> dict[str, Any]:
        markets = self._markets or {}
        market = markets.get(canonical_id)
        if market is None:
            raise DataAdapterError(f"{self.exchange_id} does not list {canonical_id!r}")
        return market

    async def aclose(self) -> None:
        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

    # ------------------------------------------------------------------ api

    async def list_symbols(self) -> list[SymbolInfo]:
        markets = await self._load_markets()
        out: list[SymbolInfo] = []
        for symbol, market in markets.items():
            if not market.get("swap") and not market.get("future"):
                continue
            if market.get("quote") not in {"USDT", "USDC", "USD"}:
                continue
            market_class = MarketClass.CRYPTO_PERP if market.get("swap") else MarketClass.CRYPTO_FUTURES
            out.append(
                SymbolInfo(
                    canonical_id=symbol,
                    display=f"{symbol} ({self.exchange_id})",
                    market_class=market_class,
                    venue=self.exchange_id.upper(),
                    provider_symbols={"ccxt": symbol},
                )
            )
        out.sort(key=lambda s: s.canonical_id)
        return out

    async def get_instrument_metadata(self, canonical_id: str) -> InstrumentMetadata:
        await self._load_markets()
        market = self._market(canonical_id)
        limits = market.get("limits") or {}
        precision = market.get("precision") or {}
        tick_size = precision.get("price")
        if isinstance(tick_size, float):
            tick_size = float(tick_size)
        contract_size = market.get("contractSize")
        min_amount = (limits.get("amount") or {}).get("min")
        min_price = (limits.get("price") or {}).get("min")
        return InstrumentMetadata(
            canonical_id=canonical_id,
            symbol=canonical_id,
            market_class=MarketClass.CRYPTO_PERP if market.get("swap") else MarketClass.CRYPTO_FUTURES,
            venue=self.exchange_id.upper(),
            quote_currency=str(market.get("quote") or ""),
            base_currency=str(market.get("base") or ""),
            digits=None,
            tick_size=float(tick_size) if tick_size else None,
            contract_size=float(contract_size) if contract_size else None,
            min_stop_distance=None,
            typical_spread=None,
            price_grid=None,
            trading_hours={"mode": "24/7"},
            value_per_price_unit=float(contract_size) if contract_size else None,
            extra={
                "id": market.get("id"),
                "type": market.get("type"),
                "linear": market.get("linear"),
                "min_amount": min_amount,
                "min_price": min_price,
                "settle": market.get("settle"),
            },
        )

    async def get_candles(
        self,
        canonical_id: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        closed_only: bool = True,
    ) -> list[Candle]:
        await self._load_markets()
        self._market(canonical_id)
        ex = await self._ex()
        ccxt_tf = _CCXT_TF[timeframe]
        supported = getattr(ex, "timeframes", None) or {}
        if supported and ccxt_tf not in supported:
            fallback = self._pick_resample_source(supported, timeframe)
            if fallback is None:
                raise DataAdapterError(
                    f"{self.exchange_id} does not support {timeframe.value} and no supported "
                    f"lower timeframe divides it"
                )
            ratio = timeframe.seconds // fallback.seconds
            want_base = min(_DOCUMENTED_MAX, int(limit) * ratio) if limit else _MAX_FETCH
            base = await self._fetch(canonical_id, fallback, want_base, start, end)
            resampled = resample_candles(base, fallback, timeframe)
            if closed_only:
                resampled = [c for c in resampled if c.is_closed]
            if limit:
                resampled = resampled[-int(limit):]
            return resampled
        want = int(limit) if limit else _MAX_FETCH
        since_ms = int(start.timestamp() * 1000) if start else None
        return await self._fetch(canonical_id, timeframe, want, start, end, end_ms_hint=end, closed_only=closed_only, limit=limit)

    def _pick_resample_source(self, supported: dict, target: Timeframe) -> Timeframe | None:
        candidates = [tf for tf in (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30,
                                    Timeframe.H1, Timeframe.H2, Timeframe.H3, Timeframe.H4)
                      if _CCXT_TF.get(tf) in supported and can_resample(tf, target)]
        return max(candidates, key=lambda tf: tf.seconds) if candidates else None

    async def _fetch(
        self,
        canonical_id: str,
        timeframe: Timeframe,
        want: int,
        start: datetime | None,
        end: datetime | None,
        *,
        end_ms_hint: datetime | None = None,
        closed_only: bool = True,
        limit: int | None = None,
    ) -> list[Candle]:
        ccxt_tf = _CCXT_TF[timeframe]
        ex = await self._ex()
        rows: list[list[float]] = []
        since_ms = int(start.timestamp() * 1000) if start else None
        while True:
            batch = await ex.fetch_ohlcv(canonical_id, timeframe=ccxt_tf, since=since_ms, limit=min(_MAX_FETCH, want))
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < min(_MAX_FETCH, want) or len(rows) >= want:
                break
            since_ms = int(batch[-1][0]) + 1
            if since_ms > int(utcnow().timestamp() * 1000):
                break
        rows.sort(key=lambda r: r[0])
        now = utcnow()
        end_ms = int((end or end_ms_hint).timestamp() * 1000) if (end or end_ms_hint) else None
        out: list[Candle] = []
        seen: set[int] = set()
        for row in rows:
            if len(row) < 5:
                continue
            ts_ms = int(row[0])
            if ts_ms in seen:
                continue
            seen.add(ts_ms)
            if end_ms is not None and ts_ms > end_ms:
                continue
            open_time = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            is_closed = (open_time + timedelta(seconds=timeframe.seconds)) <= now
            if closed_only and not is_closed:
                continue
            out.append(
                Candle(
                    symbol=canonical_id,
                    timeframe=timeframe,
                    open_time=open_time,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    tick_volume=None,
                    real_volume=float(row[5]) if len(row) > 5 and row[5] is not None else None,
                    is_closed=is_closed,
                    source=f"ccxt:{self.exchange_id}",
                )
            )
        if limit:
            out = out[-int(limit):]
        return out

    async def get_funding_history(self, canonical_id: str, since: datetime | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Funding rate history where the venue provides it (public endpoint)."""
        ex = await self._ex()
        if not getattr(ex, "has", {}).get("fetchFundingRateHistory"):
            raise DataAdapterError(f"{self.exchange_id} does not expose funding rate history via ccxt")
        since_ms = int(since.timestamp() * 1000) if since else None
        data = await ex.fetch_funding_rate_history(canonical_id, since=since_ms, limit=limit)
        return list(data)

    async def subscribe(self, canonical_id: str) -> AsyncIterator[Tick]:  # type: ignore[override]
        raise DataAdapterError(
            "live streaming for crypto is not implemented in Phase 0; candles are fetched by polling. "
            "Phase 1 adds a websocket stream."
        )
        yield  # pragma: no cover