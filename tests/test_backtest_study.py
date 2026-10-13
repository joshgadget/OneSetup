"""Phase 6 tests: the module study, its random-anchor control and the no-look-ahead walk.

The control test is the important one: on a series that is a pure random walk by
construction, the same entries and stops without the pattern condition must hit the
target about 1/(1+R) of the time, and the simulator must not be able to beat that.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from engine.backtest.candidates import CANDIDATE_BUILDERS, MODULE_ORDER, SeriesContext
from engine.backtest.metrics import compute_metrics
from engine.backtest.study import random_anchor_control, run_study, signals_in_split
from engine.backtest.splits import time_split
from engine.config import load_params
from engine.models import Candle, Direction, Timeframe
from engine.modules.common import make_signal

params = load_params()
T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def random_walk_candles(n: int = 400, seed: int = 7) -> list[Candle]:
    rng = np.random.default_rng(seed)
    price = 100.0
    candles = []
    for i in range(n):
        step = float(rng.normal(0.0, 1.0))
        open_ = price
        close = open_ + step
        high = max(open_, close) + abs(step) * 0.4
        low = min(open_, close) - abs(step) * 0.4
        candles.append(Candle(symbol="WALK", timeframe=Timeframe.H1,
                              open_time=T0 + timedelta(hours=i),
                              open=open_, high=high, low=low, close=close))
        price = close
    return candles


def synthetic_signals(candles, *, every: int = 3, risk: float = 1.0, reward_risk: float = 2.0):
    out = []
    for i in range(25, len(candles) - 45, every):
        long_ = (i // every) % 2 == 0
        entry = candles[i].close
        if long_:
            stop, target = entry - risk, entry + risk * reward_risk
        else:
            stop, target = entry + risk, entry - risk * reward_risk
        out.append(make_signal(
            module="fixture", version="0.0.0", symbol="WALK", timeframe=Timeframe.H1,
            direction=Direction.LONG if long_ else Direction.SHORT,
            entry=entry, stop=stop, target=target, candle=candles[i], known_index=i,
            candles=candles,
        ))
    return out


def test_random_anchor_control_sits_near_the_analytic_baseline():
    candles = random_walk_candles(600, seed=11)
    signals = synthetic_signals(candles)
    controls = random_anchor_control(signals, candles, params, seed=5)
    metrics = compute_metrics(controls)
    assert metrics.resolved > 100
    assert metrics.win_rate is not None
    assert abs(metrics.win_rate - 1.0 / 3.0) < 0.10, (
        f"a random walk must not beat p0: got {metrics.win_rate:.3f}")


def test_signals_in_split_only_takes_signals_known_inside_it():
    candles = random_walk_candles(300, seed=3)
    signals = synthetic_signals(candles)
    splits = time_split(len(candles))
    test = splits["test"]
    picked = signals_in_split(signals, candles, test)
    for signal in picked:
        index = [c for c, candle in enumerate(candles) if candle.open_time == signal.known_time][0]
        assert test.contains(index)
    assert len(picked) < len(signals)


def test_run_study_reports_every_module_without_a_lucky_verdict():
    candles = random_walk_candles(500, seed=4)
    ctx = SeriesContext(candles=candles, params=params)
    reports = run_study(ctx)
    assert set(reports) == set(MODULE_ORDER)
    for name, report in reports.items():
        assert report.module == name
        body = report.to_dict()
        assert body["signals"] == report.signals
        assert report.metrics.trades == report.trades
        assert report.trades <= report.signals or report.signals == 0
        assert report.passed is not True or (
            report.test is not None
            and report.test.resolved >= int(params.gate.min_trades_for_verdict)), (
            f"{name} claimed a verdict without enough resolved test trades")


def test_every_module_builder_returns_the_shared_record():
    candles = random_walk_candles(500, seed=6)
    ctx = SeriesContext(candles=candles, params=params)
    for name, builder in CANDIDATE_BUILDERS.items():
        for signal in builder(ctx):
            assert signal.module_name, name
            assert signal.known_time >= signal.formation_time
            assert abs(signal.target - signal.entry) > 0
