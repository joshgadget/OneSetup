"""Phase 6 tests: the Wilson interval as the spec writes it, and the trade metrics."""
from __future__ import annotations

import math

import pytest

from engine.backtest.metrics import compute_metrics, wilson_interval


class FakeTrade:
    def __init__(self, r_net, outcome="target", entry=100.0, stop=99.0, target=102.0,
                 exit_index=0, bars_in_trade=1, cost_r=0.0, funding_r=0.0, ambiguous=False):
        self.r_net = r_net
        self.outcome = outcome
        self.entry = entry
        self.stop = stop
        self.target = target
        self.exit_index = exit_index
        self.bars_in_trade = bars_in_trade
        self.cost_r = cost_r
        self.funding_r = funding_r
        self.ambiguous = ambiguous


def test_wilson_interval_matches_the_spec_formula():
    z = 1.96
    k, n = 40, 100
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    low, high = wilson_interval(k, n)
    assert low == pytest.approx(centre - half, abs=1e-12)
    assert high == pytest.approx(centre + half, abs=1e-12)
    assert low == pytest.approx(0.30938, abs=1e-4)
    assert high == pytest.approx(0.49798, abs=1e-4)


def test_wilson_interval_edges():
    assert wilson_interval(0, 10)[0] == 0.0
    assert wilson_interval(10, 10)[1] == 1.0
    with pytest.raises(ValueError):
        wilson_interval(1, 0)
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_metrics_are_the_spec_definitions():
    trades = [
        FakeTrade(2.0, "target", exit_index=1),
        FakeTrade(-1.0, "stop", exit_index=2),
        FakeTrade(2.0, "target", exit_index=3),
        FakeTrade(-1.0, "stop", exit_index=4),
        FakeTrade(0.5, "expired", exit_index=5),
    ]
    metrics = compute_metrics(trades)
    assert metrics.trades == 5
    assert metrics.resolved == 4
    assert metrics.wins == 2
    assert metrics.win_rate == pytest.approx(0.5)
    assert metrics.profit_factor == pytest.approx(4.0 / 2.0)
    assert metrics.expectancy_resolved_r == pytest.approx(0.5)
    assert metrics.expectancy_r == pytest.approx(0.5)
    assert metrics.expired == 1
    assert metrics.max_drawdown_r == pytest.approx(1.0), "the peak of +2 falls to +1"
    assert metrics.baseline_p0 == pytest.approx(1 / 3)


def test_metrics_report_nothing_without_trades():
    metrics = compute_metrics([])
    assert metrics.trades == 0 and metrics.win_rate is None
    assert metrics.profit_factor is None and metrics.expectancy_r is None
    assert metrics.max_drawdown_r == 0.0


def test_beats_baseline_uses_the_wilson_lower_bound():
    good = [FakeTrade(2.0, "target", exit_index=i) for i in range(60)]
    good += [FakeTrade(-1.0, "stop", exit_index=100 + i) for i in range(40)]
    metrics = compute_metrics(good)
    assert metrics.baseline_p0 == pytest.approx(1 / 3)
    assert metrics.beats_baseline is True, "0.6 hit rate on a 1:2 setup clears p0"

    weak = [FakeTrade(2.0, "target", exit_index=i) for i in range(35)]
    weak += [FakeTrade(-1.0, "stop", exit_index=100 + i) for i in range(65)]
    assert compute_metrics(weak).beats_baseline is False
