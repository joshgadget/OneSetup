"""FastAPI application.

Phase 0 endpoints: health, the symbol catalogue, instrument metadata and candles.
The catalogue is the seed registry plus everything discovery has stored, filtered by
query so the site can drive a search box over hundreds of instruments.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from engine.backtest.candidates import SeriesContext
from engine.backtest.costs import CostModel
from engine.config import get_settings, load_params
from engine.data.base import DataAdapterError
from engine.data.discovery import discover_and_store
from engine.data.integrity import find_gaps, is_stale
from engine.data.registry import all_symbols
from engine.data.resample import can_resample, resample_candles
from engine.foundation import compute_foundation
from engine.models import SymbolInfo, Timeframe
from services.api.analysis import ANALYSIS_VERSION, analyse_context, overlays_from_context
from services.api.deps import adapter_for_symbol, find_symbol, get_ccxt, get_deriv, get_store

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Close the provider clients on shutdown so no session is left open."""
    yield
    try:
        await get_ccxt().aclose()
    except Exception:  # pragma: no cover - best effort at shutdown
        pass


app = FastAPI(title="Trading Analysis API", version="0.4.0", lifespan=lifespan)

_ANALYSIS_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 600.0
# A symbol with almost no stored history cannot be analysed; say so plainly instead of
# failing somewhere deeper with an empty split or a divisor by zero.
MIN_OVERLAY_CANDLES = 30
MIN_ANALYSIS_CANDLES = 200
# Providers are polled by the browser, so the same symbol and timeframe can only
# reach a provider once inside this window; inside it the store answers.
PROVIDER_MIN_INTERVAL_SECONDS = 20.0

# Markets that do not trade every day: a weekly candle is complete at five daily
# members, not seven (DECISIONS.md D-016). Everything else trades continuously.
SESSION_MARKETS = {"forex", "gold", "commodity", "index"}


def expected_members_for(market_class, tf: Timeframe) -> int | None:
    """How many base candles a complete bucket needs, when the venue has no such timeframe."""
    if tf is not Timeframe.W1:
        return None
    return 5 if market_class.value in SESSION_MARKETS else 7

_LAST_PROVIDER_CALL: dict[tuple[str, str], float] = {}


def _cache_get(key: tuple) -> dict | None:
    entry = _ANALYSIS_CACHE.get(key)
    if entry is None:
        return None
    stored_at, value = entry
    if time.time() - stored_at > _CACHE_TTL_SECONDS:
        _ANALYSIS_CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: tuple, value: dict) -> None:
    if len(_ANALYSIS_CACHE) > 64:
        oldest = min(_ANALYSIS_CACHE, key=lambda k: _ANALYSIS_CACHE[k][0])
        _ANALYSIS_CACHE.pop(oldest, None)
    _ANALYSIS_CACHE[key] = (time.time(), value)
CORS_ORIGINS = get_settings().cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    params = load_params()
    settings = get_settings()
    return {
        "status": "ok",
        "analysis_version": ANALYSIS_VERSION,
        "params_version": params.version,
        "params_sha256": params.file_hash,
        "providers": {
            "fxcm_token": bool(settings.fxcm_access_token),
            "fxcm_note": "retired transport, see DECISIONS.md D-006",
            "deriv_app_id": bool(settings.deriv_app_id),
            "deriv_public_test_app_id": not settings.deriv_app_id,
            "ccxt": settings.crypto_exchange,
        },
        "forex_venue": settings.forex_venue.upper(),
        "gold_venue": settings.gold_venue.upper(),
    }


def _catalogue() -> dict[str, SymbolInfo]:
    """Store wins over the seed registry: discovery knows more than the seeds do."""
    merged = {s.canonical_id: s for s in all_symbols()}
    for info in get_store().list_symbols():
        existing = merged.get(info.canonical_id)
        if existing is not None:
            aliases = {**existing.provider_symbols, **info.provider_symbols}
            tags = {**existing.tags, **info.tags}
            info = info.model_copy(update={"provider_symbols": aliases, "tags": tags})
        merged[info.canonical_id] = info
    return merged


def _matches(info: SymbolInfo, needle: str) -> bool:
    needle = needle.lower()
    haystack = [info.canonical_id, info.display, *info.provider_symbols.values(), *info.tags.values()]
    return any(needle in str(value).lower() for value in haystack)


@app.get("/symbols")
def symbols(
    q: str | None = Query(default=None, description="substring match on id, display name or provider alias"),
    venue: str | None = None,
    market_class: str | None = None,
    with_data: bool = Query(default=False, description="only symbols that already have stored candles"),
    limit: int = Query(default=2000, ge=1, le=20000),
) -> dict:
    counts = get_store().candle_counts()
    rows: list[dict] = []
    for info in _catalogue().values():
        meta = counts.get(info.canonical_id) or {}
        if venue and info.venue.upper() != venue.upper():
            continue
        if market_class and info.market_class.value != market_class.lower():
            continue
        if q and not _matches(info, q):
            continue
        candles = int(meta.get("candles") or 0)
        if with_data and candles == 0:
            continue
        rows.append({
            **info.model_dump(),
            "candles": candles,
            "last_candle": meta.get("last_candle"),
            "has_data": candles > 0,
        })
    rows.sort(key=lambda r: (r["market_class"], r["canonical_id"]))
    return {"count": len(rows), "symbols": rows[:limit]}


@app.post("/symbols/refresh")
async def refresh_symbols() -> dict:
    """Ask each provider what it currently lists and store the answer.

    Providers that fail are reported with their error, never silently skipped.
    """
    report = await discover_and_store(get_store(), {"deriv": get_deriv(), "ccxt": get_ccxt()})
    report["catalogue_size"] = len(_catalogue())
    return report


@app.get("/instrument/{canonical_id:path}")
async def instrument(canonical_id: str) -> dict:
    store = get_store()
    meta = store.get_instrument(canonical_id)
    if meta is None:
        try:
            meta = await adapter_for_symbol(canonical_id).get_instrument_metadata(
                (find_symbol(canonical_id).canonical_id if find_symbol(canonical_id) else canonical_id))
        except DataAdapterError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        store.upsert_instrument(meta)
    return meta.model_dump()


@app.get("/candles")
async def candles(
    symbol: str,
    timeframe: str = "H1",
    limit: int = Query(default=500, ge=1, le=10000),
    store_only: bool = False,
    refresh: bool = False,
) -> dict:
    try:
        tf = Timeframe.parse(timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    info = find_symbol(symbol)
    if info is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r}; see GET /symbols")
    canonical = info.canonical_id
    store = get_store()
    rows = store.get_candles(canonical, tf, limit=limit)
    source = "store"
    refreshed = False
    refresh_skipped: str | None = None
    if store_only:
        pass
    elif not rows or refresh:
        if rows and refresh and _provider_throttled(canonical, tf):
            refresh_skipped = (f"provider calls for this symbol and timeframe are limited to "
                               f"one every {int(PROVIDER_MIN_INTERVAL_SECONDS)}s; the store "
                               f"answered instead")
        else:
            try:
                fetched, fetched_source = await _fetch_candles(info, tf, limit)
            except DataAdapterError as exc:
                if not rows:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
                refresh_skipped = str(exc)
            else:
                _provider_called(canonical, tf)
                if fetched:
                    store.upsert_candles(fetched)
                    rows = store.get_candles(canonical, tf, limit=limit)
                    source = fetched_source
                    refreshed = True
    gaps = find_gaps(rows, tf, info.market_class) if rows else []
    stale = is_stale(rows[-1], tf) if rows else True
    return {
        "symbol": canonical,
        "display": info.display,
        "market_class": info.market_class.value,
        "venue": info.venue,
        "timeframe": tf.value,
        "count": len(rows),
        "source": source,
        "refreshed": refreshed,
        "refresh_skipped": refresh_skipped,
        "stale": stale,
        "unexpected_gaps": [str(g) for g in gaps if g.kind == "unexpected"],
        "expected_gaps": [str(g) for g in gaps if g.kind != "unexpected"],
        "candles": [
            {
                "time": c.open_time.isoformat(),
                "open": c.open, "high": c.high, "low": c.low, "close": c.close,
                "volume": c.real_volume if c.real_volume is not None else c.tick_volume,
                "closed": c.is_closed, "source": c.source,
            }
            for c in rows
        ],
    }


@app.get("/timeframes")
def timeframes() -> dict:
    return {"timeframes": [t.value for t in Timeframe]}


def _provider_throttled(canonical: str, tf: Timeframe) -> bool:
    last = _LAST_PROVIDER_CALL.get((canonical, tf.value))
    return last is not None and (time.time() - last) < PROVIDER_MIN_INTERVAL_SECONDS


def _provider_called(canonical: str, tf: Timeframe) -> None:
    _LAST_PROVIDER_CALL[(canonical, tf.value)] = time.time()


async def _fetch_candles(info, tf: Timeframe, bars: int) -> tuple[list[Candle], str]:
    """Candles from the provider, with a resample fallback for unsupported timeframes.

    Deriv publishes no weekly candles, so a timeframe a venue does not serve falls back
    to the largest stored or fetchable base timeframe that divides it. The resampler
    drops incomplete buckets rather than guessing, and every returned candle keeps the
    closed flag, so a weekly candle appears only once its week has actually ended.
    """
    adapter = adapter_for_symbol(info.canonical_id)
    try:
        fetched = await adapter.get_candles(info.canonical_id, tf, limit=bars)
        return fetched, info.venue.lower()
    except DataAdapterError as first_error:
        expected = expected_members_for(info.market_class, tf)
        for base_tf in (Timeframe.D1, Timeframe.H4, Timeframe.H1):
            if not can_resample(base_tf, tf):
                continue
            store = get_store()
            base = store.get_candles(info.canonical_id, base_tf, limit=max(bars * 8, 500))
            if not base:
                try:
                    base = await adapter.get_candles(info.canonical_id, base_tf,
                                                    limit=max(bars * 8, 500))
                except DataAdapterError:
                    continue
                if base:
                    store.upsert_candles(base)
            derived = (resample_candles(base, base_tf, tf, expected_members=expected)
                       if base else [])
            if derived:
                return derived, f"{info.venue.lower()}+resample"
        raise DataAdapterError(
            f"{info.venue} does not serve {tf.value} for {info.canonical_id} and it cannot "
            f"be resampled from stored candles ({first_error})") from first_error


async def _series(symbol: str, timeframe: str, bars: int, *, refresh: bool = False):
    """Closed candles for one symbol, from the store or freshly fetched.

    Unfinished candles never leave this function: the spec's first rule is that no
    detector and no client sees a candle that has not closed.
    """
    try:
        tf = Timeframe.parse(timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    info = find_symbol(symbol)
    if info is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r}; see GET /symbols")
    store = get_store()
    canonical = info.canonical_id
    rows = store.get_candles(canonical, tf, limit=bars)
    source = "store"
    throttled = _provider_throttled(canonical, tf)
    if (not rows or refresh) and not throttled:
        try:
            fetched, fetched_source = await _fetch_candles(info, tf, bars)
        except DataAdapterError as exc:
            if not rows:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        else:
            _provider_called(canonical, tf)
            if fetched:
                store.upsert_candles(fetched)
                rows = store.get_candles(canonical, tf, limit=bars)
                source = fetched_source
    if not rows:
        raise HTTPException(status_code=404,
                            detail=f"no candles for {info.canonical_id} {tf.value}")
    return info, tf, rows, source


def _build_context(info, candles, tf):
    store = get_store()
    instrument = store.get_instrument(info.canonical_id)
    params = load_params()
    ctx = SeriesContext(candles=candles, params=params)
    atr = ctx.foundation.atr_at(len(candles) - 1)
    costs = CostModel.from_instrument(instrument, params, atr=atr)
    return ctx, costs


@app.get("/overlays/{symbol:path}")
async def overlays(
    symbol: str,
    timeframe: str = "H1",
    bars: int = Query(default=700, ge=50, le=5000),
    refresh: bool = False,
) -> dict:
    """Detected levels, zones and events, ready to draw on the chart."""
    info, tf, rows, source = await _series(symbol, timeframe, bars, refresh=refresh)
    if len(rows) < MIN_OVERLAY_CANDLES:
        raise HTTPException(
            status_code=409,
            detail=(f"only {len(rows)} closed {tf.value} candles stored for "
                    f"{info.canonical_id}; the detectors need at least "
                    f"{MIN_OVERLAY_CANDLES}. Load history for this symbol first."))
    ctx, _costs = await asyncio.to_thread(_build_context, info, rows, tf)
    body = await asyncio.to_thread(overlays_from_context, ctx)
    body.update({
        "display": info.display,
        "market_class": info.market_class.value,
        "venue": info.venue,
        "source": source,
    })
    return body


@app.get("/gate/{symbol:path}")
async def gate(
    symbol: str,
    timeframe: str = "H1",
    bars: int = Query(default=1200, ge=200, le=5000),
    refresh: bool = False,
) -> dict:
    """The symbol gate on its own, before any setup is shown."""
    analysis = await analysis_endpoint(symbol, timeframe=timeframe, bars=bars, refresh=refresh)
    return {
        "symbol": analysis["symbol"],
        "display": analysis.get("display"),
        "timeframe": analysis["timeframe"],
        "as_of": analysis["as_of"],
        "gate": analysis["gate"],
    }


@app.get("/analysis/{symbol:path}")
async def analysis_endpoint(
    symbol: str,
    timeframe: str = "H1",
    bars: int = Query(default=1200, ge=200, le=5000),
    refresh: bool = False,
) -> dict:
    """One setup with its entry, stop, target, reasons and evidence, or "no setup"."""
    info, tf, rows, source = await _series(symbol, timeframe, bars)
    if len(rows) < MIN_ANALYSIS_CANDLES:
        raise HTTPException(
            status_code=409,
            detail=(f"only {len(rows)} closed {tf.value} candles stored for "
                    f"{info.canonical_id}; the analysis needs at least "
                    f"{MIN_ANALYSIS_CANDLES} to split in time and test the gate. "
                    "Load history for this symbol first."))
    params = load_params()
    key = (info.canonical_id, tf.value, rows[-1].open_time.isoformat(), len(rows),
           params.file_hash)
    if not refresh:
        cached = _cache_get(key)
        if cached is not None:
            return {**cached, "cached": True}
    ctx, costs = await asyncio.to_thread(_build_context, info, rows, tf)
    body = await asyncio.to_thread(analyse_context, ctx, costs=costs)
    gaps = find_gaps(rows, tf, info.market_class)
    body.update({
        "display": info.display,
        "market_class": info.market_class.value,
        "venue": info.venue,
        "source": source,
        "stale": is_stale(rows[-1], tf),
        "unexpected_gaps": [str(g) for g in gaps if g.kind == "unexpected"],
        "costs": {"spread_price": costs.spread_price,
                  "slippage_price": costs.slippage_price,
                  "commission_price": costs.commission_price,
                  "notes": list(costs.notes)},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cached": False,
    })
    _cache_put(key, body)
    return body