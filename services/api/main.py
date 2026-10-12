"""FastAPI application. Phase 0 endpoints: health, symbols, instrument, candles."""
from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from engine.config import get_settings, load_params
from engine.data.base import DataAdapterError
from engine.data.integrity import find_gaps, is_stale
from engine.data.registry import all_symbols, get_symbol
from engine.models import Timeframe
from services.api.deps import adapter_for_symbol, get_store

app = FastAPI(title="Trading Analysis API", version="0.1.0")
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
            "fxcm": bool(settings.fxcm_access_token),
            "deriv": bool(settings.deriv_app_id),
            "ccxt": settings.crypto_exchange,
        },
    }


@app.get("/symbols")
def symbols() -> dict:
    stored = {s.canonical_id: s for s in get_store().list_symbols()}
    merged = {s.canonical_id: s for s in all_symbols()}
    merged.update(stored)
    return {"symbols": [s.model_dump() for s in merged.values()]}


@app.get("/instrument/{canonical_id}")
async def instrument(canonical_id: str) -> dict:
    store = get_store()
    meta = store.get_instrument(canonical_id)
    if meta is None:
        try:
            meta = await adapter_for_symbol(canonical_id).get_instrument_metadata(canonical_id)
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
    if get_symbol(symbol) is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r}; see GET /symbols")
    store = get_store()
    rows = store.get_candles(symbol, tf, limit=limit)
    if not rows and not store_only:
        try:
            fetched = await adapter_for_symbol(symbol).get_candles(symbol, tf, limit=limit)
        except DataAdapterError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if fetched:
            store.upsert_candles(fetched)
        rows = fetched
    info = get_symbol(symbol)
    gaps = find_gaps(rows, tf, info.market_class) if rows else []
    stale = is_stale(rows[-1], tf) if rows else True
    return {
        "symbol": symbol,
        "timeframe": tf.value,
        "count": len(rows),
        "stale": stale,
        "unexpected_gaps": [str(g) for g in gaps if g.kind == "unexpected"],
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