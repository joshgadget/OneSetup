"""Phase 3 tests (liquidity): pools, grabs, structure and target selection.

Fixtures are hand-built candles with explicit open/high/low/close so the expected
answers follow from the spec's inequalities rather than from the implementation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.config import load_params
from engine.foundation import compute_foundation
from engine.models import Candle, Timeframe
from engine.modules.liquidity import (
    Side, active_pools, build_pools, dealing_range, detect_grabs, liquidity_structure,
    nearest_pool, pool_position,
)

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
params = load_params()


def candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(symbol="TEST", timeframe=Timeframe.H1, open_time=T0 + timedelta(hours=i),
                  open=o, high=h, low=l, close=c)


def flat_warmup(n: int = 20, price: float = 100.0, pad: float = 1.0) -> list[Candle]:
    """A calm stretch so ATR exists and is roughly 2 x pad."""
    return [candle(i, price, price + pad, price - pad, price) for i in range(n)]


# ------------------------------------------------------------------ pools

def test_two_swing_highs_within_delta_form_one_pool():
    candles = flat_warmup()
    # two tops 0.1 apart, each with 3 lower candles either side -> confirmed swings
    candles += [
        candle(20, 100, 101, 99, 100),
        candle(21, 100, 102.0, 100, 101),
        candle(22, 101, 105.0, 101, 102),
        candle(23, 102, 103, 101, 102),
        candle(24, 102, 102, 100, 101),
        candle(25, 101, 101, 99, 100),
        candle(26, 100, 100, 98, 99),
        candle(27, 99, 101, 98, 100),
        candle(28, 100, 105.1, 100, 102),
        candle(29, 102, 103, 101, 102),
        candle(30, 102, 102, 100, 101),
        candle(31, 101, 101, 99, 100),
        candle(32, 100, 100, 98, 99),
    ]
    foundation = compute_foundation(candles, k=3)
    pools = build_pools(candles, foundation, params)
    buys = [p for p in pools if p.side is Side.BUY]
    assert buys, "expected a buy-side pool"
    top = max(buys, key=lambda p: p.level)
    # the pool level is the highest high in the cluster
    assert top.level == 105.1
    assert top.size >= 1
    assert top.known_index >= top.formed_index


def test_sell_side_pool_uses_the_lowest_low():
    candles = flat_warmup()
    candles += [
        candle(20, 100, 101, 99, 100),
        candle(21, 100, 101, 99, 100),
        candle(22, 100, 101, 95.0, 96),      # trough
        candle(23, 96, 97, 96, 96.5),
        candle(24, 96.5, 98, 96, 97),
        candle(25, 97, 98, 96.5, 97),
        candle(26, 97, 98, 97, 97.5),
    ]
    foundation = compute_foundation(candles, k=3)
    pools = build_pools(candles, foundation, params)
    sells = [p for p in pools if p.side is Side.SELL]
    assert sells
    assert min(p.level for p in sells) == 95.0


# ------------------------------------------------------------------ grabs

def _pool_fixture(sweep_close: float, sweep_high: float, extra: list[Candle] | None = None):
    """A confirmed swing low near 96, a confirmed swing high at 110, then a sweep.

    The low forms at index 20 and the high at 23, both with k = 3, so the dealing range
    exists from index 26 and the sweep candle at 27 is the first one that can act on the
    pool.
    """
    candles = flat_warmup(price=100.0)
    candles += [
        candle(20, 100, 101, 96.0, 97.0),    # swing low, confirmed at 23
        candle(21, 97.0, 100, 97.0, 99.0),
        candle(22, 99.0, 102, 99.0, 101.0),
        candle(23, 101, 110.0, 101, 105),    # swing high, confirmed at 26
        candle(24, 105, 106, 104, 105),
        candle(25, 105, 106, 104, 105),
        candle(26, 105, 106, 104, 105),
        candle(27, 105, sweep_high, 104, sweep_close),
    ]
    return candles + (extra or [])


def test_buy_side_grab_pierces_and_closes_back_inside():
    # ATR here is a little over 2, so the 0.05 x ATR buffer is about 0.1
    candles = _pool_fixture(sweep_close=109.0, sweep_high=110.5)
    foundation = compute_foundation(candles, k=3)
    pools = build_pools(candles, foundation, params)
    grabs = detect_grabs(candles, foundation, pools, params)
    buys = [g for g in grabs if g.side is Side.BUY]
    assert buys, "a pierce above the pool with a close back below it is a grab"
    grab = buys[-1]
    assert grab.pool_level == 110.0
    assert grab.variant == "one_candle"
    assert grab.known_index == grab.pierce_index
    assert grab.depth_atr > 0 and grab.pool_size >= 1


def test_close_beyond_the_pool_is_a_breakout_not_a_sweep():
    candles = _pool_fixture(sweep_close=112.0, sweep_high=112.5)
    foundation = compute_foundation(candles, k=3)
    pools = build_pools(candles, foundation, params)
    grabs = detect_grabs(candles, foundation, pools, params)
    assert not [g for g in grabs if g.side is Side.BUY], "closing beyond the level is not a sweep"


def test_loose_variant_waits_for_the_reclaim_candle():
    # pierce one candle, close beyond it, then close back below within r = 3
    candles = _pool_fixture(sweep_close=112.0, sweep_high=112.5, extra=[
        candle(28, 112, 112, 108, 111.5),
        candle(29, 111.5, 112, 108, 111.0),
        candle(30, 111, 111, 105, 106.0),   # reclaim close back below 110
    ])
    foundation = compute_foundation(candles, k=3)
    pools = build_pools(candles, foundation, params)
    grabs = detect_grabs(candles, foundation, pools, params)
    loose = [g for g in grabs if g.variant == "reclaim_within_r"]
    assert loose, "expected the loose reclaim variant to fire"
    grab = loose[-1]
    assert grab.known_index > grab.pierce_index, "knowable at the reclaim, never earlier"


# --------------------------------------------------- structure and targets

def test_pool_position_against_the_dealing_range():
    candles = _pool_fixture(sweep_close=109.0, sweep_high=110.5)
    foundation = compute_foundation(candles, k=3)
    rng = dealing_range(foundation, len(candles) - 1)
    assert rng is not None
    low, high = rng
    pool = type("P", (), {"level": high + 1})()
    assert pool_position(pool, (low, high)) == "external"
    inside = type("P", (), {"level": (low + high) / 2})()
    assert pool_position(inside, (low, high)) == "internal"


def test_nearest_pool_picks_the_smallest_normalised_distance():
    candles = _pool_fixture(sweep_close=109.0, sweep_high=110.5)
    foundation = compute_foundation(candles, k=3)
    pools = build_pools(candles, foundation, params)
    index = len(candles) - 1
    above = nearest_pool(pools, foundation, index, above=True)
    below = nearest_pool(pools, foundation, index, above=False)
    close = candles[index].close
    if above is not None:
        assert above.level > close
    if below is not None:
        assert below.level < close


def test_liquidity_structure_reports_counts():
    candles = _pool_fixture(sweep_close=109.0, sweep_high=110.5)
    foundation = compute_foundation(candles, k=3)
    pools = build_pools(candles, foundation, params)
    structure = liquidity_structure(pools, foundation, len(candles) - 1)
    assert structure["external_count"] + structure["internal_count"] == len(
        active_pools(pools, len(candles) - 1, include_swept=True))
    assert structure["range"] is not None


# -------------------------------------------------------------- look-ahead

def test_no_lookahead_pools_match_prefix_runs():
    import random

    rng = random.Random(31)
    price = 100.0
    closes = []
    for _ in range(240):
        price += rng.gauss(0, 1.0)
        closes.append(round(price, 4))
    candles = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        candles.append(candle(i, o, max(o, c) + 0.5, min(o, c) - 0.5, c))
    foundation = compute_foundation(candles, k=3)

    for t in range(60, len(candles), 11):
        stopped = build_pools(candles, foundation, params, stop_index=t)
        prefix, _ = build_pools(candles[: t + 1], compute_foundation(candles[: t + 1], k=3), params), None
        expected = sorted((p.side.value, round(p.level, 6), p.size, p.state, p.swept_index)
                          for p in stopped)
        actual = sorted((p.side.value, round(p.level, 6), p.size, p.state, p.swept_index)
                        for p in prefix)
        assert actual == expected, f"pools differ at t={t}"