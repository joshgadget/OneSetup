"""Phase 2 tests: support and resistance discovery, bounce counting, strength.

The specification's acceptance test is that the module reproduces roughly a 0.5 bounce
probability on simulated random walks (Chung and Bellotti report about 0.5 on shuffled
returns). It does, with a measured caveat that is asserted here and reported in
DECISIONS.md D-008:

    random anchors (control)   0.5204  over 4,045 episodes across 7 seeds
    pivot anchors (as shipped) 0.5473  over 11,988 episodes across 7 seeds

The 2.7-point gap is a selection effect, not a bug: a level anchored on a confirmed
swing extreme sits where price recently turned, and price inside such a band is
disproportionately at a local extreme, so it leaves back through the near edge slightly
more often than half the time even with no order flow at all. That is the noise floor
the symbol gate has to beat, and it is why the gate may not treat "better than 0.5" as
evidence on its own.

Every fixture here is a synthetic random walk or a hand-built path used as a control.
Nothing in this file is market evidence.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from engine.config import load_params
from engine.foundation import compute_foundation
from engine.models import Candle, Timeframe
from engine.modules.support_resistance import (
    Level, LevelKind, _advance, discover_levels, gamma_series,
)
from engine.scoring.stats import beta_posterior_mean, beta_posterior_variance, wilson_interval

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
HORIZON = 5


def make_candles(closes: list[float], pad: float = 0.5) -> list[Candle]:
    out = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i else close
        out.append(Candle(
            symbol="TEST", timeframe=Timeframe.H1, open_time=T0 + timedelta(hours=i),
            open=open_, high=max(open_, close) + pad, low=min(open_, close) - pad, close=close,
        ))
    return out


def random_walk(n: int, seed: int = 5, step: float = 1.0) -> list[float]:
    rng = random.Random(seed)
    price = 1000.0
    out = []
    for _ in range(n):
        price += rng.gauss(0, step)
        out.append(round(price, 4))
    return out


def drive(level: Level, path: list[float], atr: float = 200.0, buffer: float = 0.1,
          horizon: int = HORIZON) -> int:
    """Push a level through a close path. Returns the number of signal triggers.

    atr defaults to 200, so the break buffer is 20 and the break check cannot fire:
    these tests isolate the episode logic.
    """
    triggers = 0
    for i, close in enumerate(path):
        candle = make_candles([close], pad=0.4)[0]
        if _advance(level, candle, i, atr, buffer, horizon):
            triggers += 1
    return triggers


params = load_params()


# ------------------------------------------------------------------ strength

def test_beta_posterior_matches_the_published_formula():
    assert beta_posterior_mean(0, 0) == pytest.approx(0.5)      # no history = baseline
    assert beta_posterior_mean(3, 1) == pytest.approx(4 / 6)    # (n+1)/(N+2)
    assert beta_posterior_mean(1, 0) == pytest.approx(2 / 3)
    # Var = (n+1)(N-n+1) / ((N+3)(N+2)^2), n = 3, N = 4
    assert beta_posterior_variance(3, 1) == pytest.approx((4 * 2) / (7 * 36))


# ------------------------------------------------------------ episode counting

def test_support_episodes_resolve_through_near_and_far_edges():
    """Support [95, 105] with a 5-candle horizon.

    110 -> 100 opens an episode (inside), 108 leaves through the near edge (bounce).
    100 -> 90 opens another, 90 leaves through the far edge (penetration).
    """
    level = Level(LevelKind.SUPPORT, lower=95.0, upper=105.0, gamma=5.0,
                  formed_index=0, known_index=0)
    triggers = drive(level, [110.0, 100.0, 108.0, 100.0, 90.0, 110.0])
    assert level.inside_count == 2
    assert level.bounces == 1
    assert level.penetrations == 1
    assert level.strength == pytest.approx(0.5)     # (1+1)/(2+2)
    assert triggers == 1, "one close back out through the near edge"


def test_resistance_episodes_mirror_support():
    level = Level(LevelKind.RESISTANCE, lower=95.0, upper=105.0, gamma=5.0,
                  formed_index=0, known_index=0)
    triggers = drive(level, [90.0, 100.0, 92.0, 100.0, 112.0, 100.0, 92.0])
    assert level.inside_count == 3
    assert level.bounces == 2        # 100 -> 92 twice: out through the near edge
    assert level.penetrations == 1   # 100 -> 112: out through the far edge
    assert triggers == 2


def test_episodes_expire_when_price_stays_inside():
    """An episode still inside after omega candles is excluded from p(b), not counted."""
    level = Level(LevelKind.SUPPORT, lower=95.0, upper=105.0, gamma=5.0,
                  formed_index=0, known_index=0)
    drive(level, [110.0, 100.0, 101.0, 99.0, 100.0, 101.0, 100.0, 102.0, 101.0])
    assert level.bounces == 0 and level.penetrations == 0
    assert level.expired_count >= 1
    assert level.strength == pytest.approx(0.5)


def test_break_confirmation_needs_a_close_beyond_the_buffer():
    level = Level(LevelKind.RESISTANCE, lower=95.0, upper=105.0, gamma=5.0,
                  formed_index=0, known_index=0, position="inside", last_close=100.0)
    wick_only = Candle(symbol="T", timeframe=Timeframe.H1, open_time=T0, open=100.0,
                       high=110.0, low=99.0, close=104.0)
    _advance(level, wick_only, 1, atr=2.0, break_buffer_atr=0.1, horizon=HORIZON)
    assert level.state == "active", "a wick beyond the level must not break it"
    closing = Candle(symbol="T", timeframe=Timeframe.H1, open_time=T0, open=104.0,
                     high=106.0, low=103.0, close=105.5)
    _advance(level, closing, 2, atr=2.0, break_buffer_atr=0.1, horizon=HORIZON)
    assert level.state == "broken" and level.broken_index == 2


# ------------------------------------------------------------------- discovery

def test_gamma_trailing_mean_uses_only_past_candles():
    candles = make_candles(random_walk(50, seed=2))
    foundation = compute_foundation(candles)
    gammas = gamma_series(candles, foundation, mode="trailing_mean_abs_diff", window=20)
    assert gammas[0] is None
    assert gammas[1] == pytest.approx(abs(candles[1].close - candles[0].close))
    assert gammas[25] is not None and gammas[25] > 0


def test_gamma_atr_mode_tracks_atr():
    candles = make_candles(random_walk(40, seed=4))
    foundation = compute_foundation(candles)
    gammas = gamma_series(candles, foundation, mode="atr_multiple", atr_multiple=0.25)
    assert gammas[12] is None                     # no ATR before the 14-candle seed
    assert gammas[13] == pytest.approx(foundation.atr[13] * 0.25)


def test_levels_come_from_confirmed_swings():
    candles = make_candles(random_walk(400, seed=9))
    foundation = compute_foundation(candles)
    levels, _ = discover_levels(candles, foundation, params)
    assert levels, "expected some levels on a 400-candle walk"
    for lv in levels:
        assert lv.known_index >= lv.formed_index
        assert lv.lower < lv.upper
        assert lv.swing_indices
        assert lv.known_index - lv.formed_index >= foundation.k


def test_merged_levels_freeze_the_originals():
    candles = make_candles(random_walk(600, seed=23))
    levels, _ = discover_levels(candles, compute_foundation(candles), params, collect_signals=False)
    merged = [lv for lv in levels if lv.state == "merged"]
    assert merged, "a 600-candle walk should produce at least one merge"
    for lv in merged:
        assert lv.absorbed_into is None or lv.absorbed_into >= 0
        # frozen: a level that already merged must not keep counting
        before = (lv.bounces, lv.penetrations, lv.lower, lv.upper)
        for i in range(lv.known_index + 1, len(candles)):
            _advance(lv, candles[i], i, None, 0.1, HORIZON)
        assert (lv.bounces, lv.penetrations, lv.lower, lv.upper) == before


def test_signals_respect_the_rule():
    candles = make_candles(random_walk(800, seed=13))
    _, signals = discover_levels(candles, compute_foundation(candles), params)
    assert signals, "the rule should fire sometimes, otherwise it proves nothing"
    for sig in signals:
        assert sig.known_time >= sig.formation_time
        assert abs(sig.entry - sig.stop) > 0
        if sig.direction.value == "long":
            assert sig.target > sig.entry > sig.stop
        else:
            assert sig.target < sig.entry < sig.stop
        assert sig.module_name == "support_resistance"
        assert sig.probability is None, "no calibrated model exists yet: score, never a probability"
        assert sig.features["level_strength"] > float(params.support_resistance.strength_floor)


# ------------------------------------------------------------- the 0.5 control

@pytest.mark.parametrize("seed", [1, 2, 3])
def test_bounce_probability_on_random_walks_is_near_one_half(seed):
    """Driftless random walk: no order clusters, so no real edge can exist.

    Measured 0.5473 pooled over seven seeds with pivot anchoring, against 0.5204 with
    random anchoring (see the module docstring). The band below is wide enough to hold
    that selection effect and too tight to hold a bug: an error in the counting showed
    up at 0.92 during development.
    """
    candles = make_candles(random_walk(4000, seed=seed, step=1.0))
    levels, _ = discover_levels(candles, compute_foundation(candles), params, collect_signals=False)
    bounces = sum(lv.bounces for lv in levels)
    penetrations = sum(lv.penetrations for lv in levels)
    total = bounces + penetrations
    assert total > 400, f"too few resolved episodes to judge: {total}"
    rate = bounces / total
    assert 0.48 <= rate <= 0.60, f"bounce rate {rate:.3f} on {total} episodes (seed {seed})"


def test_pooled_bounce_probability_stays_within_the_measured_noise_band():
    """Pooling seven seeds gives a tight interval; it must not drift from the measured floor."""
    bounces = penetrations = 0
    for seed in (1, 2, 3, 7, 11, 21, 42):
        candles = make_candles(random_walk(4000, seed=seed))
        levels, _ = discover_levels(candles, compute_foundation(candles), params, collect_signals=False)
        bounces += sum(lv.bounces for lv in levels)
        penetrations += sum(lv.penetrations for lv in levels)
    total = bounces + penetrations
    rate = bounces / total
    assert 0.51 <= rate <= 0.58, f"pooled rate {rate:.4f} over {total} episodes"
    low, _ = wilson_interval(bounces, total)
    assert low > 0.5, "the noise floor really is above 0.5, which the gate must account for"


# ------------------------------------------------------------- no look-ahead

def test_no_lookahead_levels_match_prefix_runs():
    """A longer series must not change what the scan already knew at candle t.

    The comparison is between the scan stopped at t on the full series and a scan of
    the truncated series, so the level counts are final in both runs. If any part of
    the scan read a later candle, these diverge.
    """
    candles = make_candles(random_walk(300, seed=17))
    foundation = compute_foundation(candles)
    for t in range(60, len(candles), 7):
        stopped_levels, _ = discover_levels(
            candles, foundation, params, collect_signals=False, stop_index=t)
        prefix_candles = candles[: t + 1]
        prefix_levels, _ = discover_levels(
            prefix_candles, compute_foundation(prefix_candles), params,
            collect_signals=False)
        expected = sorted(
            (lv.kind.value, round(lv.lower, 6), round(lv.upper, 6), lv.bounces,
             lv.penetrations, lv.expired_count, lv.state, lv.broken_index)
            for lv in stopped_levels
        )
        actual = sorted(
            (lv.kind.value, round(lv.lower, 6), round(lv.upper, 6), lv.bounces,
             lv.penetrations, lv.expired_count, lv.state, lv.broken_index)
            for lv in prefix_levels
        )
        assert actual == expected, f"levels differ at t={t}"


def test_no_lookahead_signals_match_prefix_runs():
    candles = make_candles(random_walk(400, seed=19))
    _, full_signals = discover_levels(candles, compute_foundation(candles), params)
    for t in (150, 250, 399):
        prefix = candles[: t + 1]
        _, prefix_signals = discover_levels(prefix, compute_foundation(prefix), params)
        expected = [(s.known_time, s.direction.value, s.entry, s.stop, s.target)
                    for s in full_signals if s.known_time <= candles[t].open_time]
        actual = [(s.known_time, s.direction.value, s.entry, s.stop, s.target) for s in prefix_signals]
        assert actual == expected, f"signals differ at t={t}"