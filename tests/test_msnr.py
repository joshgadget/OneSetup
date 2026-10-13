"""Phase 4 tests (MSNR): close levels, zones, gaps, freshness, bounce counting, setup v1."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.config import load_params
from engine.foundation import compute_foundation
from engine.models import Candle, Direction, Timeframe
from engine.modules.msnr import (
    advance_levels, close_levels, detect_levels, detect_msnr_setup_v1, gap_levels,
)
from engine.modules.smc import StructureEvent

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
params = load_params()


def candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(symbol="TEST", timeframe=Timeframe.H1, open_time=T0 + timedelta(hours=i),
                  open=o, high=h, low=l, close=c)


def flat(i: int, close: float = 100.0) -> Candle:
    return candle(i, close, close + 1, close - 1, close)


def _level_series() -> list[Candle]:
    """A V-level at 98 and an A-level at 100.5 on closes, k = 1."""
    candles = [flat(i) for i in range(20)]
    candles += [
        candle(20, 100.0, 100.2, 97.8, 98.0),     # V-level, confirmed at 21
        candle(21, 98.0, 99.0, 97.9, 98.5),
        candle(22, 98.5, 100.0, 98.4, 99.5),
        candle(23, 99.5, 100.7, 99.4, 100.5),     # A-level, confirmed at 24
        candle(24, 100.5, 100.6, 99.3, 99.5),
        candle(25, 99.5, 100.2, 99.4, 100.0),
        candle(26, 98.0, 98.7, 97.9, 98.6),       # touch of the V zone, close above it
        candle(27, 98.6, 99.2, 98.5, 99.0),
        candle(28, 99.0, 100.0, 98.8, 99.8),
    ]
    return candles


def test_close_levels_use_the_close_and_confirm_one_candle_later():
    candles = _level_series()
    foundation = compute_foundation(candles, k=3)
    levels = close_levels(candles, foundation, params)
    v = [lv for lv in levels if lv.kind == "V"]
    a = [lv for lv in levels if lv.kind == "A"]
    assert v[0].price == 98.0 and v[0].index == 20 and v[0].known_index == 21
    assert a[0].price == 100.5 and a[0].index == 23 and a[0].known_index == 24
    assert all(lv.price in {c.close for c in candles} for lv in levels)
    # the level is knowable at t + k, never at t
    early = close_levels(candles[:21], compute_foundation(candles[:21], k=3), params)
    assert not [lv for lv in early if lv.kind == "V" and lv.price == 98.0]


def test_zone_width_is_the_larger_of_the_tick_floor_and_atr_share():
    candles = _level_series()
    foundation = compute_foundation(candles, k=3)
    levels = close_levels(candles, foundation, params, tick_size=0.0001)
    v = [lv for lv in levels if lv.kind == "V"][0]
    assert abs(v.gamma - 0.1 * foundation.atr_at(20)) < 1e-12
    wide = close_levels(candles, foundation, params, tick_size=1.0)
    v2 = [lv for lv in wide if lv.kind == "V"][0]
    assert v2.gamma == 3.0, "3 ticks at a tick size of 1.0 beat the ATR share"
    assert v2.lower == 98.0 - 3.0 and v2.upper == 98.0 + 3.0


def test_gap_level_uses_theta_and_sits_at_the_gap_midpoint():
    candles = [flat(i) for i in range(20)]
    candles.append(candle(20, 101.0, 101.2, 100.5, 100.5))   # opens 1.0 above the prior close
    foundation = compute_foundation(candles, k=3)
    gaps = gap_levels(candles, foundation, params)
    assert len(gaps) == 1
    gap = gaps[0]
    atr = foundation.atr_at(20)
    assert abs(gap.price - 100.5) < 1e-12
    assert gap.meta["gap_size_atr"] == abs(1.0) / atr
    assert gap.known_index == 20
    assert abs(1.0) >= float(params.msnr.gap_theta_atr) * atr


def test_touches_bounces_breaks_and_the_bayesian_score():
    candles = _level_series()
    candles += [
        candle(29, 99.8, 99.9, 97.5, 97.6),      # breaks the 98 zone on a close
    ]
    foundation = compute_foundation(candles, k=3)
    levels = detect_levels(candles, foundation, params, tick_size=0.0001)
    v = [lv for lv in levels if lv.kind == "V" and lv.price == 98.0 and lv.index == 20][0]
    assert 26 in v.touch_indices, "any candle range intersecting the zone is a touch"
    assert v.bounces == 1, "the touch at 26 resolved with a close back above the zone"
    assert v.bounce_probability == (v.bounces + 1) / (v.resolved + 2)
    assert abs(v.bounce_probability - 2.0 / 3.0) < 1e-12
    # the close at 29 breaks the far edge by more than the ATR buffer
    atr29 = foundation.atr_at(29)
    assert candles[29].close < v.lower - 0.1 * atr29
    assert v.invalidated_index == 29
    assert not v.fresh
    lam = float(params.msnr.freshness_lambda)
    assert abs(v.freshness_score(lam) - __import__("math").exp(-lam * v.touches)) < 1e-12


def test_setup_v1_requires_the_touch_the_bullish_close_and_a_follow_up():
    candles = _level_series()
    foundation = compute_foundation(candles, k=3)
    levels = detect_levels(candles, foundation, params, tick_size=0.0001)
    events = [StructureEvent("CHoCH", "bull", 28, 96.0, 20, "bear")]
    signals = detect_msnr_setup_v1(candles, foundation, params, levels=levels,
                                   smc_events=events, mss_events=[])
    assert len(signals) == 1
    signal = signals[0]
    assert signal.direction is Direction.LONG
    assert signal.known_time == candles[28].open_time
    assert signal.entry == candles[28].close == 99.8
    v_zone = [lv for lv in levels if lv.kind == "V" and lv.price == 98.0 and lv.index == 20][0]
    atr = foundation.atr_at(28)
    assert abs(signal.stop - (v_zone.lower - 0.25 * atr)) < 1e-12
    target_level = min((lv for lv in levels if lv.kind == "A" and lv.lower > signal.entry),
                       key=lambda lv: lv.lower)
    assert signal.target == target_level.lower
    assert signal.meta["follow_up"] == "CHoCH"

    late = [StructureEvent("CHoCH", "bull", 33, 96.0, 20, "bear")]
    candles_long = candles + [flat(i) for i in range(29, 34)]
    foundation_long = compute_foundation(candles_long, k=3)
    levels_long = detect_levels(candles_long, foundation_long, params, tick_size=0.0001)
    assert not detect_msnr_setup_v1(candles_long, foundation_long, params,
                                    levels=levels_long, smc_events=late,
                                    mss_events=[]), "r = 5 candles is the limit"


def test_msnr_prefix_consistency():
    import random

    rng = random.Random(23)
    price = 100.0
    candles = []
    for i in range(200):
        price += rng.gauss(0, 0.9)
        pad = abs(rng.gauss(0, 0.5)) + 0.1
        candles.append(candle(i, price - rng.gauss(0, 0.3),
                              price + pad, price - pad, price))
    foundation = compute_foundation(candles, k=3)
    for t in range(40, len(candles), 19):
        stopped = detect_levels(candles, foundation, params, stop_index=t)
        prefix = detect_levels(candles[: t + 1], compute_foundation(candles[: t + 1], k=3), params)
        key = lambda lv: (lv.kind, round(lv.price, 6), lv.index, lv.known_index,
                          len(lv.touch_indices), lv.bounces, lv.penetrations,
                          lv.invalidated_index)
        assert sorted(map(key, stopped)) == sorted(map(key, prefix)), f"levels differ at t={t}"