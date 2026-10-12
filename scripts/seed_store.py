"""Load the four Phase 0 symbols into the candle store, across the standard timeframes.

Phase 0 acceptance: GBPUSD, XAUUSD, one crypto perpetual and one Deriv index load
into the store and can be charted. Requires FXCM_ACCESS_TOKEN for FX/gold and
DERIV_APP_ID for the synthetic index; crypto needs no key.

Usage:
    python scripts/seed_store.py --limit 1000
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.config import get_settings
from engine.data.base import DataAdapterError
from engine.data.registry import all_symbols, get_symbol
from engine.data.store import CandleStore
from engine.models import Timeframe
from services.api.deps import adapter_for

TIMEFRAMES = [Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15]
SEED = ["GBPUSD", "XAUUSD", "BTC/USD:BTC", "R_75"]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1000, help="candles per timeframe")
    ap.add_argument("--symbols", default=",".join(SEED), help="comma separated canonical ids")
    args = ap.parse_args()
    targets = [x.strip() for x in args.symbols.split(",") if x.strip()]
    store = CandleStore(get_settings().db_path)
    for info in all_symbols():
        store.upsert_symbol(info)

    failures: list[str] = []
    for canonical in targets:
        info = get_symbol(canonical)
        if info is None:
            failures.append(f"{canonical}: not in registry")
            continue
        adapter = adapter_for(info)
        try:
            meta = await adapter.get_instrument_metadata(canonical)
            store.upsert_instrument(meta)
            print(f"{canonical}: metadata ok (venue {meta.venue}, tick {meta.tick_size})")
        except (DataAdapterError, Exception) as exc:  # noqa: BLE001
            failures.append(f"{canonical}: metadata failed: {type(exc).__name__}: {exc}")
            continue
        for tf in TIMEFRAMES:
            try:
                candles = await adapter.get_candles(canonical, tf, limit=args.limit)
            except (DataAdapterError, Exception) as exc:  # noqa: BLE001 - report, never hide
                failures.append(f"{canonical} {tf.value}: {type(exc).__name__}: {exc}")
                continue
            n = store.upsert_candles(candles)
            total = store.count(canonical, tf)
            print(f"{canonical:14s} {tf.value:3s} fetched {len(candles):5d} stored {n:5d} total {total:6d}")
    closed: set[str] = set()
    for x in targets:
        info = get_symbol(x)
        if info is None or info.canonical_id in closed:
            continue
        closed.add(info.canonical_id)
        try:
            await adapter_for(info).aclose()
        except Exception:  # noqa: BLE001
            pass
    if failures:
        print("\nFailures (provider keys are the usual cause):")
        for f in failures:
            print("  -", f)
        return 1
    print("\nSeed complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))