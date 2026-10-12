"""Live data smoke test: proves the feeds are real before any analysis is built.

Nothing here is simulated. Providers without credentials in .env are reported as
SKIPPED, never faked. Raw provider payloads are printed so the output can be
audited line by line.

Usage:
    python scripts/smoke_data.py            # print evidence only
    python scripts/smoke_data.py --save     # also store the fetched candles
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.config import get_settings
from engine.data.crypto_ccxt import CCXTAdapter
from engine.data.deriv import DerivAdapter
from engine.data.fxcm import FXCMAdapter
from engine.data.store import CandleStore
from engine.models import Timeframe


def line(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


async def smoke_fxcm(save: bool, store: CandleStore | None) -> bool:
    s = get_settings()
    if not s.fxcm_access_token:
        line("FXCM: SKIPPED (no FXCM_ACCESS_TOKEN in .env)")
        return False
    line(f"FXCM @ {s.fxcm_host} (price side: {s.fxcm_price_side})")
    adapter = FXCMAdapter(access_token=s.fxcm_access_token, host=s.fxcm_host, price_side=s.fxcm_price_side)
    try:
        sid = await adapter._ensure_session()
        print(f"socket handshake ok, sid length={len(sid)}")

        syms = await adapter.list_symbols()
        print(f"symbols listed: {len(syms)}; first five: {[x.display for x in syms[:5]]}")

        raw = await adapter.raw("GET", "/trading/get_model/", params={"models": "Offer"})
        keys = list(raw.keys())
        print(f"get_model(Offer) envelope keys: {keys}")

        meta = await adapter.get_instrument_metadata("GBPUSD")
        print("GBPUSD metadata:", json.dumps(meta.model_dump(), default=str)[:400])

        offer_id, provider_symbol = await adapter._offer_id("GBPUSD")
        raw_c = await adapter.raw("GET", f"/candles/{offer_id}/h1/", params={"num": 3})
        print("raw candle rows (as returned, newest last):")
        for row in (raw_c.get("candles") or [])[:3]:
            print("   ", row)

        candles = await adapter.get_candles("GBPUSD", Timeframe.H1, limit=1000)
        gold = await adapter.get_candles("XAUUSD", Timeframe.H4, limit=500)
        print(f"GBPUSD H1 closed candles: {len(candles)}")
        if candles:
            print(f"   first {candles[0].open_time.isoformat()}  last {candles[-1].open_time.isoformat()}")
            print(f"   last close {candles[-1].close}")
        print(f"XAUUSD H4 closed candles: {len(gold)}")
        if gold:
            print(f"   last close {gold[-1].close} at {gold[-1].open_time.isoformat()}")
        if save and store is not None:
            n = store.upsert_candles(candles) + store.upsert_candles(gold)
            store.upsert_instrument(meta)
            print(f"saved {n} candle rows for GBPUSD/XAUUSD")
        return True
    finally:
        await adapter.aclose()


async def smoke_ccxt(save: bool, store: CandleStore | None) -> bool:
    s = get_settings()
    line(f"CRYPTO via CCXT: {s.crypto_exchange} (public endpoints, no key needed)")
    adapter = CCXTAdapter(exchange=s.crypto_exchange)
    try:
        syms = await adapter.list_symbols()
        perps = [x for x in syms if x.canonical_id.startswith("BTC/")]
        print(f"swap symbols listed: {len(syms)}; BTC markets: {[x.display for x in perps[:3]]}")
        symbol = perps[0].canonical_id if perps else "BTC/USD:BTC"
        try:
            meta = await adapter.get_instrument_metadata(symbol)
            print("BTC/USDT:USDT metadata:", json.dumps(meta.model_dump(), default=str)[:400])
        except Exception as exc:  # noqa: BLE001
            print(f"metadata unavailable for {symbol}: {exc}")
            return False
        candles = await adapter.get_candles(symbol, Timeframe.H1, limit=300)
        print(f"{symbol} H1 closed candles: {len(candles)}")
        if candles:
            print(f"   first {candles[0].open_time.isoformat()}  last {candles[-1].open_time.isoformat()}")
            print(f"   last close {candles[-1].close}, volume {candles[-1].real_volume}")
        funding = await adapter.get_funding_history(symbol, limit=3)
        print("recent funding:", json.dumps(funding[:3], default=str)[:400])
        if save and store is not None:
            n = store.upsert_candles(candles)
            store.upsert_instrument(meta)
            print(f"saved {n} candle rows for {symbol}")
        return True
    finally:
        await adapter.aclose()


async def smoke_deriv(save: bool, store: CandleStore | None) -> bool:
    s = get_settings()
    if not s.deriv_app_id:
        line("DERIV: SKIPPED (no DERIV_APP_ID in .env)")
        return False
    line(f"DERIV synthetic indices (app_id set, url={DerivAdapter(app_id=s.deriv_app_id).url.split('?')[0]})")
    adapter = DerivAdapter(app_id=s.deriv_app_id)
    syms = await adapter.list_symbols()
    vol = [x for x in syms if x.canonical_id.startswith("R_") or x.canonical_id.startswith("1HZ")]
    print(f"symbols listed: {len(syms)}; volatility examples: {[x.canonical_id for x in vol[:6]]}")
    candles = await adapter.get_candles("R_75", Timeframe.M15, limit=300)
    print(f"R_75 M15 closed candles: {len(candles)}")
    if candles:
        print(f"   first {candles[0].open_time.isoformat()}  last {candles[-1].open_time.isoformat()}")
        print(f"   last close {candles[-1].close}")
    meta = await adapter.get_instrument_metadata("R_75")
    print("R_75 metadata:", json.dumps(meta.model_dump(), default=str)[:400])
    if save and store is not None:
        n = store.upsert_candles(candles)
        store.upsert_instrument(meta)
        print(f"saved {n} candle rows for R_75")
    return True


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="store fetched candles in the DB")
    args = ap.parse_args()
    store = CandleStore(get_settings().db_path) if args.save else None
    results = {}
    results["fxcm"] = await smoke_fxcm(args.save, store)
    results["ccxt"] = await smoke_ccxt(args.save, store)
    results["deriv"] = await smoke_deriv(args.save, store)
    line("SUMMARY")
    for k, v in results.items():
        print(f"{k:8s} {'OK' if v else 'skipped/failed'}")
    print("\nA provider that is skipped or failed above is NOT evidence about the others.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))