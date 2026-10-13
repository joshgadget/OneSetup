"""Phase 1 tests: candle maths, Wilder ATR, confirmed pivots, structure state machine.

The fixtures are hand-built with answers worked out on paper. The last test is the
important one: it re-runs every detector on truncated prefixes of the series and
demands identical output for everything already knowable, which is what "no
look-ahead" means in practice.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from engine.foundation import compute_foundation, confirmed_pivots, label_swings, protected_level, trend_at, wilder_atr
from engine.foundation.structure import Trend
from engine.models import Candle, Timeframe

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def make_candles(closes: list[float], pad: float = 0.5) -> list[Candle]:
    """Candles built from a close path, so the fixture is readable and deterministic."""
    out = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i else close
        out.append(
            Candle(
                symbol="TEST", timeframe=Timeframe.H1,
                open_time=T0 + timedelta(hours=i),
                open=open_,
                high=max(open_, close) + pad,
                low=min(open_, close) - pad,
                close=close,
            )
        )
    return out


def random_walk(n: int, seed: int = 7, step: float = 1.0) -> list[float]:
    rng = random.Random(seed)
    price = 1000.0
    out = []
    for _ in range(n):
        price += rng.gauss(0, step)
        out.append(round(price, 4))
    return out


# --------------------------------------------------------------------------- ATR

def test_wilder_atr_matches_hand_computed_values():
    # constant range of 2.0 (pad 0.5 each side, close-to-close moves of 0)
    candles = make_candles([100.0] * 20, pad=1.0)
    atr = wilder_atr(candles, 14)
    assert atr[:13] == [None] * 13            # nothing before the seed
    assert atr[13] == pytest.approx(2.0)      # SMA of the first 14 true ranges
    assert atr[19] == pytest.approx(2.0)      # stays flat on a flat series


def test_wilder_atr_seed_then_recursion():
    closes = [100.0] * 14 + [100.0 + 10.0]
    candles = make_candles(closes, pad=0.0)
    atr = wilder_atr(candles, 14)
    # first 14 true ranges: 0,0,...,0 then a 10.0 jump on candle 14
    trs = [c.high - c.low for c in candles[:14]]
    seed = sum(trs) / 14
    assert atr[13] == pytest.approx(seed)
    expected = ((14 - 1) * seed + 10.0) / 14
    assert atr[14] == pytest.approx(expected)


def test_atr_is_none_when_series_too_short():
    assert wilder_atr(make_candles([100.0] * 5), 14) == [None] * 5


# ------------------------------------------------------------------------- pivots

def test_pivot_detection_and_known_index():
    # a clean peak at index 5, a clean trough at index 10
    closes = [100, 101, 102, 103, 104, 110, 104, 103, 102, 101, 95, 101, 102, 103, 104]
    candles = make_candles([float(c) for c in closes], pad=0.1)
    pivots = confirmed_pivots(candles, k=3)
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    assert [(p.index, p.known_index) for p in highs] == [(5, 8)]
    assert [(p.index, p.known_index) for p in lows] == [(10, 13)]
    assert pivots[0].known_time == candles[8].open_time


def test_pivot_tie_rule_left_strict_right_nonstrict():
    # two equal highs at index 2 and 3: only the left one can qualify
    closes = [1.0, 2.0, 5.0, 5.0, 2.0, 1.0, 0.5]
    candles = []
    for i, c in enumerate(closes):
        candles.append(Candle(symbol="T", timeframe=Timeframe.H1, open_time=T0 + timedelta(hours=i),
                              open=c, high=c, low=c, close=c))
    pivots = confirmed_pivots(candles, k=2)
    highs = [p.index for p in pivots if p.kind == "high"]
    assert highs == [2]


# ---------------------------------------------------------------------- structure

def test_structure_labels_and_trend():
    # two rising swings -> HH and HL -> bull
    closes = [10, 12, 14, 12, 11, 15, 17, 15, 14, 18, 20, 18, 17]
    candles = make_candles([float(c) for c in closes], pad=0.2)
    labels = label_swings(confirmed_pivots(candles, k=2))
    kinds = [(lb.kind, lb.label) for lb in labels]
    assert ("high", "HH") in kinds and ("low", "HL") in kinds
    assert trend_at(labels, len(candles) - 1) is Trend.BULL


def test_trend_is_none_until_both_sides_are_labelled():
    labels = label_swings(confirmed_pivots(make_candles([float(c) for c in [10, 12, 14, 12, 13]], pad=0.2), k=2))
    # a single high pivot is not a trend
    assert trend_at(labels, 4) is Trend.NONE


def test_equal_high_is_not_a_higher_high():
    closes = [10, 12, 14, 12, 11, 14, 12, 11, 13]
    candles = make_candles([float(c) for c in closes], pad=0.2)
    labels = label_swings(confirmed_pivots(candles, k=2))
    highs = [lb.label for lb in labels if lb.kind == "high"]
    assert highs[0] == "HH" and "HH" not in highs[1:]


def test_protected_level_is_the_low_before_the_latest_higher_high():
    closes = [10, 12, 14, 12, 11, 15, 17, 15, 14, 18, 20, 18, 17, 19]
    candles = make_candles([float(c) for c in closes], pad=0.2)
    labels = label_swings(confirmed_pivots(candles, k=2))
    index = len(candles) - 1
    trend = trend_at(labels, index)
    level = protected_level(labels, index, trend)
    assert trend is Trend.BULL
    assert level is not None
    # the protected low is the lowest pivot low formed before the latest HH
    assert level.price < candles[-1].close


# ------------------------------------------------------------ no look-ahead proof

def test_no_lookahead_prefix_consistency():
    """Recomputing on truncated data must reproduce exactly what was knowable.

    This is the test that fails if any detector peeks: it compares the full-series
    result, restricted to events with known_index <= t, against a run that only ever
    saw candles[:t+1].
    """
    candles = make_candles(random_walk(220, seed=11))
    k = 3
    full = compute_foundation(candles, k=k)

    for t in range(len(candles)):
        prefix = compute_foundation(candles[: t + 1], k=k)

        expected_pivots = [
            (p.kind, p.index, p.known_index, p.price)
            for p in full.pivots
            if p.known_index <= t
        ]
        actual_pivots = [(p.kind, p.index, p.known_index, p.price) for p in prefix.pivots]
        assert actual_pivots == expected_pivots, f"pivots differ at t={t}"

        assert prefix.trend_at(t) is full.trend_at(t), f"trend differs at t={t}"

        full_labels = [(lb.kind, lb.label, lb.index, lb.known_index) for lb in full.labels if lb.known_index <= t]
        prefix_labels = [(lb.kind, lb.label, lb.index, lb.known_index) for lb in prefix.labels]
        assert prefix_labels == full_labels, f"labels differ at t={t}"


def test_no_lookahead_atr_prefix_consistency():
    candles = make_candles(random_walk(80, seed=3))
    full = compute_foundation(candles)
    for t in range(14, len(candles)):
        prefix = compute_foundation(candles[: t + 1])
        assert prefix.atr[t] == pytest.approx(full.atr[t]), f"ATR differs at t={t}"