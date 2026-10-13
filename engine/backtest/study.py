"""Run every module alone against the baseline, on one symbol and timeframe.

The spec's protocol step 5: compare against a baseline of the same entries and stops
without the pattern condition. Two baselines are reported:

* p0 = 1/(1+R) per trade, the analytic driftless-random-walk hit rate for that reward
  to risk, averaged over the module's own setups;
* a random-anchor control: the same entries, stops, targets, expiry and costs, anchored
  at uniform random candles in the same period, so a trending period cannot hand a
  module its edge for free.

A module passes when the lower end of the Wilson interval on its out-of-sample hit rate
clears its own average p0. Below gate.min_trades_for_verdict resolved trades the study
reports no verdict rather than a lucky one.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

from engine.config import load_params
from engine.models import Candle, Direction, ModuleSignal
from engine.backtest.candidates import CANDIDATE_BUILDERS, MODULE_ORDER, SeriesContext
from engine.backtest.costs import CostModel
from engine.backtest.metrics import Metrics, compute_metrics
from engine.backtest.simulator import index_signals, simulate
from engine.backtest.splits import Split, time_split, walk_forward


@dataclass
class ModuleReport:
    module: str
    signals: int
    trades: int
    metrics: Metrics
    test: Metrics | None
    control: Metrics | None
    p0_mean: float | None
    passed: bool | None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "signals": self.signals,
            "trades": self.trades,
            "metrics": self.metrics.to_dict(),
            "test": self.test.to_dict() if self.test else None,
            "control": self.control.to_dict() if self.control else None,
            "p0_mean": self.p0_mean,
            "passed": self.passed,
            "notes": list(self.notes),
        }


def signals_in_split(signals: Sequence[ModuleSignal], candles: Sequence[Candle],
                     split: Split) -> list[ModuleSignal]:
    """Signals whose known candle falls inside the split. Detectors may see history."""
    positions = index_signals(signals, candles)
    return [s for s in signals if split.contains(positions[id(s)])]


def random_anchor_control(
    signals: Sequence[ModuleSignal],
    candles: Sequence[Candle],
    params,
    *,
    seed: int,
    costs: CostModel | None = None,
    funding_rate_per_day: float | None = None,
) -> list:
    """The same geometry without the pattern: uniform random anchors, same period."""
    rng = random.Random(seed)
    outcome_expiry = int(params.execution.outcome_expiry_candles)
    last_anchor = max(1, len(candles) - outcome_expiry - 1)
    controls: list[ModuleSignal] = []
    for signal in signals:
        risk = abs(signal.entry - signal.stop)
        if risk <= 0:
            continue
        reward_risk = abs(signal.target - signal.entry) / risk
        anchor = rng.randrange(0, last_anchor)
        price = candles[anchor].close
        if signal.direction is Direction.LONG:
            entry, stop, target = price, price - risk, price + risk * reward_risk
        else:
            entry, stop, target = price, price + risk, price - risk * reward_risk
        controls.append(signal.model_copy(update={
            "entry": entry, "stop": stop, "target": target,
            "known_time": candles[anchor].open_time,
            "formation_time": candles[anchor].open_time,
        }))
    return simulate(controls, candles, params, costs=costs,
                    funding_rate_per_day=funding_rate_per_day)


def run_study(
    ctx: SeriesContext,
    *,
    modules: Sequence[str] | None = None,
    costs: CostModel | None = None,
    funding_rate_per_day: float | None = None,
    walk_forward_windows: int | None = None,
) -> dict[str, ModuleReport]:
    """Build every module's candidates on one series and report each module alone."""
    params = ctx.params
    candles = ctx.candles
    names = list(modules) if modules else list(MODULE_ORDER)
    split_cfg = params.gate.split
    splits = time_split(
        len(candles),
        train_fraction=float(split_cfg.train_fraction),
        validation_fraction=float(split_cfg.validation_fraction),
        test_fraction=float(split_cfg.test_fraction),
    )
    windows = int(walk_forward_windows if walk_forward_windows is not None
                  else params.gate.walk_forward_windows)
    min_trades = int(params.gate.min_trades_for_verdict)
    seed = int(params.gate.control_seed)

    reports: dict[str, ModuleReport] = {}
    for name in names:
        builder = CANDIDATE_BUILDERS.get(name)
        if builder is None:
            raise KeyError(f"unknown module {name!r}; known: {', '.join(MODULE_ORDER)}")
        signals = list(builder(ctx))
        trades = simulate(signals, candles, params, costs=costs,
                          funding_rate_per_day=funding_rate_per_day)
        metrics = compute_metrics(trades)

        test_trades = []
        for signal in signals_in_split(signals, candles, splits["test"]):
            test_trades.extend(simulate([signal], candles, params, costs=costs,
                                        funding_rate_per_day=funding_rate_per_day))
        test_metrics = compute_metrics(test_trades) if test_trades else None

        control_trades = random_anchor_control(signals, candles, params, seed=seed,
                                               costs=costs,
                                               funding_rate_per_day=funding_rate_per_day)
        control_metrics = compute_metrics(control_trades) if control_trades else None

        notes: list[str] = []
        passed = None
        p0_mean = metrics.baseline_p0
        if test_metrics is None or test_metrics.resolved < min_trades:
            notes.append(f"no verdict: fewer than {min_trades} resolved trades in the test split")
        else:
            passed = bool(test_metrics.wilson_low is not None and p0_mean is not None
                          and test_metrics.wilson_low > p0_mean)
        folds = walk_forward(len(candles), windows=windows,
                             train_fraction=float(split_cfg.train_fraction))
        notes.append(f"walk-forward: {len(folds)} rolling folds available "
                     f"(refit in Phase 7, features only in Phase 6)")
        if costs is not None and costs.notes:
            notes.extend(costs.notes)

        reports[name] = ModuleReport(
            module=name, signals=len(signals), trades=len(trades), metrics=metrics,
            test=test_metrics, control=control_metrics, p0_mean=p0_mean,
            passed=passed, notes=tuple(notes),
        )
    return reports
