"""FastAPI application.

Phase 0 endpoints: health, the symbol catalogue, instrument metadata and candles.
The catalogue is the seed registry plus everything discovery has stored, filtered by
query so the site can drive a search box over hundreds of instruments.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from engine.config import get_settings, load_params
from engine.data.base import DataAdapterError
from engine.data.discovery import discover_and_store
from engine.data.integrity import find_gaps, is_stale
from engine.data.registry import all_symbols
from engine.models import SymbolInfo, Timeframe
from services.api.deps import adapter_for_symbol, find_symbol, get_ccxt, get_deriv, get_store

app = FastAPI(title="Trading Analysis API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    params = load_params()
    settings = get_settings()
    return {
        "status": "ok",
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


@app.get("/instrument/{canonical_id}")
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
    if not rows and not store_only:
        try:
            fetched = await adapter_for_symbol(canonical).get_candles(canonical, tf, limit=limit)
        except DataAdapterError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if fetched:
            store.upsert_candles(fetched)
        rows = fetched
        source = info.venue.lower()
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