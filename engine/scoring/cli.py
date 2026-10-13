"""Run the symbol gate on stored candles, before any module is shown.

    python -m engine.scoring.cli --symbol R_75 --timeframe H1

The gate is the spec's three checks: the variance ratio over q candles, the support and
resistance bounce probability against 0.5, and each module's out-of-sample hit rate
against its own 1/(1+R) baseline with the Wilson interval. The command prints the
verdict whether it is good news or not, and writes the whole report as JSON.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine.backtest.candidates import SeriesContext
from engine.backtest.cli import load_candles
from engine.backtest.study import run_study
from engine.config import load_params
from engine.models import Timeframe
from engine.modules import support_resistance as sr_module
from engine.scoring.gate import symbol_gate


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the symbol gate")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--bars", type=int, default=3000)
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--modules", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    params = load_params()
    timeframe = Timeframe.parse(args.timeframe)
    candles, instrument = load_candles(args.symbol, timeframe, args.bars,
                                       fetch=not args.no_fetch)
    ctx = SeriesContext(candles=candles, params=params)
    levels, _ = sr_module.discover_levels(candles, ctx.foundation, params,
                                          collect_signals=False)
    modules = [m.strip() for m in args.modules.split(",") if m.strip()] or None
    reports = run_study(ctx, modules=modules)
    metrics = {name: report.test for name, report in reports.items() if report.test}
    gate = symbol_gate(candles=candles, levels=levels, module_metrics=metrics, params=params)

    print(f"{candles[0].symbol} {timeframe.value}  {len(candles)} candles  "
          f"{candles[0].open_time:%Y-%m-%d} to {candles[-1].open_time:%Y-%m-%d}")
    vr = gate["variance_ratio"]
    print(f"\nvariance ratio ({vr['test']}, primary q={vr['primary_q']})")
    print(f"  {'q':>3}{'VR':>10}{'z':>10}{'p':>10}  verdict")
    for row in vr["q_values"]:
        verdict = ("cannot reject a random walk" if row["random_walk"] is None else
                   ("random walk" if row["random_walk"] else "not a random walk"))
        print(f"  {row['q']:>3}{_fmt(row['vr'], 4):>10}{_fmt(row['z'], 3):>10}"
              f"{_fmt(row['p'], 4):>10}  {verdict}")
    print(f"  -> {vr['verdict']}")

    bounce = gate["bounce"]
    print("\nsupport and resistance bounce probability")
    print(f"  bounces {bounce['bounces']:.0f}  penetrations {bounce['penetrations']:.0f}  "
          f"p = {_fmt(bounce['probability'])}  "
          f"interval [{_fmt((bounce['interval'] or (None, None))[0])}, "
          f"{_fmt((bounce['interval'] or (None, None))[1])}]")
    print(f"  -> {bounce['verdict']}")

    print("\nmodules against their own 1/(1+R) baseline, on the test period only")
    print(f"  {'module':<20}{'resolved':>9}{'win%':>8}{'wilson low':>12}{'p0':>8}  enabled")
    for name, row in gate["modules"].items():
        low = (row["interval"] or (None, None))[0]
        print(f"  {name:<20}{row['resolved']:>9}{_fmt(row['win_rate'], 3):>8}"
              f"{_fmt(low):>12}{_fmt(row['baseline_p0']):>8}  {row['enabled']}")

    print(f"\ngate passed: {gate['passed']}")
    print(f"verdict: {gate['verdict']}")
    for reason in gate["reasons"]:
        print(f"  - {reason}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "symbol": candles[0].symbol,
            "timeframe": timeframe.value,
            "candles": len(candles),
            "gate": gate,
        }, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
