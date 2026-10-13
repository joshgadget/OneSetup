"""Run the module-alone study on stored candles.

    python -m engine.backtest.cli --symbol frxGBPUSD --timeframe H1 --bars 3000

Candles come from the local store; if the store has none, they are fetched once from
the provider for that symbol and stored. Nothing is fabricated: when a provider cannot
answer, the command fails with the provider's error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from engine.backtest.candidates import SeriesContext
from engine.backtest.costs import CostModel
from engine.backtest.study import run_study
from engine.config import get_settings, load_params
from engine.data.base import DataAdapterError
from engine.data.resample import can_resample, resample_candles
from engine.data.store import CandleStore
from engine.models import Timeframe
from services.api.deps import adapter_for_symbol, find_symbol


def load_candles(symbol: str, timeframe: Timeframe, bars: int, *, fetch: bool) -> tuple[list, object]:
    store = CandleStore(get_settings().db_path)
    info = find_symbol(symbol)
    if info is None:
        raise SystemExit(f"unknown symbol {symbol!r}: run the API's /symbols/refresh first")
    rows = store.get_candles(info.canonical_id, timeframe, limit=bars)
    if len(rows) < bars and fetch:
        adapter = adapter_for_symbol(info.canonical_id)
        try:
            fetched = asyncio.run(adapter.get_candles(info.canonical_id, timeframe, limit=bars))
        except DataAdapterError as exc:
            raise SystemExit(f"provider could not serve {info.canonical_id} {timeframe.value}: {exc}")
        if fetched:
            store.upsert_candles(fetched)
        rows = store.get_candles(info.canonical_id, timeframe, limit=bars)
    if not rows:
        raise SystemExit(f"no candles for {info.canonical_id} {timeframe.value}")
    instrument = store.get_instrument(info.canonical_id)
    return rows, instrument


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        if value == float("inf"):
            return "inf"
        return f"{value:.{digits}f}"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run every module alone against the baseline")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--bars", type=int, default=3000)
    parser.add_argument("--modules", default="")
    parser.add_argument("--htf", default="", help="bias timeframe, resampled from the entry series")
    parser.add_argument("--funding-rate-per-day", type=float, default=None)
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    params = load_params()
    timeframe = Timeframe.parse(args.timeframe)
    candles, instrument = load_candles(args.symbol, timeframe, args.bars, fetch=not args.no_fetch)

    htf_candles = None
    htf_name = args.htf or str(params.slp.bias_timeframe)
    if htf_name and htf_name.upper() != timeframe.value:
        target = Timeframe.parse(htf_name)
        if can_resample(timeframe, target):
            htf_candles = resample_candles(candles, timeframe, target)

    reference_candles = None
    crt_tf = Timeframe.parse(str(params.crt.reference_timeframe))
    if crt_tf is not timeframe and can_resample(timeframe, crt_tf):
        reference_candles = resample_candles(candles, timeframe, crt_tf)

    tick_size = float(instrument.tick_size) if instrument and instrument.tick_size else None
    atr_last = None
    if candles:
        from engine.foundation import compute_foundation
        foundation = compute_foundation(candles, k=int(params.foundation.pivot.k_default))
        atr_last = foundation.atr_at(len(candles) - 1)
    costs = CostModel.from_instrument(instrument, params, atr=atr_last)

    ctx = SeriesContext(
        candles=candles, params=params, htf_candles=htf_candles,
        reference_candles=reference_candles, tick_size=tick_size,
    )
    modules = [m.strip() for m in args.modules.split(",") if m.strip()] or None
    reports = run_study(ctx, modules=modules, costs=costs,
                        funding_rate_per_day=args.funding_rate_per_day)

    print(f"{candles[0].symbol} {timeframe.value}  {len(candles)} candles  "
          f"{candles[0].open_time:%Y-%m-%d} to {candles[-1].open_time:%Y-%m-%d}")
    print(f"cost model: spread={costs.spread_price} slippage={costs.slippage_price} "
          f"commission={costs.commission_price}")
    for note in costs.notes:
        print(f"  note: {note}")
    header = (f"{'module':<20}{'signals':>8}{'trades':>7}{'res':>5}{'win%':>7}"
              f"{'wilson':>18}{'p0':>7}{'E[R]':>8}{'PF':>7}{'maxDD':>8}{'unfilled':>9}")
    print(header)
    print("-" * len(header))
    for name, report in reports.items():
        m = report.metrics
        wilson = f"[{_fmt(m.wilson_low)}, {_fmt(m.wilson_high)}]"
        print(f"{name:<20}{report.signals:>8}{m.trades:>7}{m.resolved:>5}"
              f"{_fmt(m.win_rate, 1):>7}{wilson:>18}{_fmt(m.baseline_p0):>7}"
              f"{_fmt(m.expectancy_r):>8}{_fmt(m.profit_factor, 2):>7}"
              f"{_fmt(m.max_drawdown_r, 2):>8}{m.unfilled:>9}")
    print()
    for name, report in reports.items():
        verdict = ("passes" if report.passed else
                   ("fails" if report.passed is False else "no verdict"))
        ctrl = report.control
        ctrl_text = (f" control win {ctrl.win_rate:.3f} on {ctrl.resolved} resolved"
                     if ctrl and ctrl.win_rate is not None else " control: none")
        test = report.test
        test_text = (f" test win {test.win_rate:.3f} on {test.resolved} resolved"
                     if test and test.win_rate is not None else " test: none")
        print(f"{name:<20} {verdict}:{test_text}{ctrl_text}")
        for note in report.notes:
            print(f"{'':<20}   - {note}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "symbol": candles[0].symbol,
            "timeframe": timeframe.value,
            "candles": len(candles),
            "start": candles[0].open_time.isoformat(),
            "end": candles[-1].open_time.isoformat(),
            "costs": {"spread_price": costs.spread_price,
                      "slippage_price": costs.slippage_price,
                      "commission_price": costs.commission_price,
                      "notes": list(costs.notes)},
            "modules": {name: report.to_dict() for name, report in reports.items()},
        }, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
