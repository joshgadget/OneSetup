"""Phase 3 tests (supply and demand): base, leg in, leg out, lines, freshness, score.

Fixtures are hand-built candles. The expected confirmation candle is recomputed in the
tests straight from the specification inequalities, not from the implementation, so the
tests fail if the module bends the rule. The method is a practitioner convention (Sam
Seiden / Online Trading Academy teaching), so these tests check the stated rules, not a
claim that the zones predict anything.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.config import load_params
from engine.foundation import compute_foundation
from engine.models import Candle, Timeframe
from engine.modules.supply_demand import (
    DEMAND, SUPPLY, detect_zones, first_touch_candidates,
)

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
params = load_params()


def candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(symbol="TEST", timeframe=Timeframe.H1, open_time=T0 + timedelta(hours=i),
                  open=o, high=h, low=l, close=c)


def warmup(n: int = 20) -> list[Candle]:
    return [candle(i, 100, 101, 99, 100) for i in range(n)]


def _demand_fixture(extra: list[Candle] | None = None) -> list[Candle]:
    """Rally of five 1.5-point candles, a two-candle base near 107.5, then a rally out."""
    candles = warmup()
    closes = [101.5, 103.0, 104.5, 106.0, 107.5]
    open_ = 100.0
    for i, close in enumerate(closes):
        candles.append(candle(20 + i, open_, close + 0.2, open_ - 0.2, close))
        open_ = close
    candles += [
        candle(25, 107.5, 108.3, 107.2, 107.9),   # base
        candle(26, 107.9, 108.2, 107.4, 107.6),   # base
        candle(27, 107.6, 109.2, 107.5, 109.0),   # leg out
        candle(28, 109.0, 110.8, 108.9, 110.6),
        candle(29, 110.6, 112.4, 110.5, 112.2),   # departure reaches 2.13 x ATR
        candle(30, 112.2, 114.2, 112.0, 114.0),
    ]
    return candles + (extra or [])


def _supply_fixture(extra: list[Candle] | None = None) -> list[Candle]:
    """The mirror image: drop in, base near 92, drop out."""
    candles = warmup()
    closes = [98.5, 97.0, 95.5, 94.0, 92.5]
    open_ = 100.0
    for i, close in enumerate(closes):
        candles.append(candle(20 + i, open_, open_ + 0.2, close - 0.2, close))
        open_ = close
    candles += [
        candle(25, 92.5, 93.0, 91.8, 92.1),
        candle(26, 92.1, 92.8, 91.9, 92.4),
        candle(27, 92.4, 92.6, 90.8, 91.0),
        candle(28, 91.0, 91.1, 89.2, 89.4),
        candle(29, 89.4, 89.5, 87.6, 87.8),
        candle(30, 87.8, 88.0, 85.8, 86.0),
    ]
    return candles + (extra or [])


def expected_confirmation(candles, foundation, base_start, base_end, kind) -> int | None:
    """First candle where the leg-out inequality holds, straight from the spec."""
    sd = params.supply_demand
    atr_e = foundation.atr_at(base_end)
    base = candles[base_start:base_end + 1]
    high = max(c.high for c in base)
    low = min(c.low for c in base)
    for j in range(base_end + 1, base_end + int(sd.leg_out_candles) + 1):
        window = candles[base_end + 1: j + 1]
        run_max = max(c.close for c in window)
        run_min = min(c.close for c in window)
        if kind == DEMAND and (run_max - high) / atr_e >= float(sd.leg_out_atr):
            return j
        if kind == SUPPLY and (low - run_min) / atr_e >= float(sd.leg_out_atr):
            return j
    return None


def pick(zones, start, end, kind):
    found = [z for z in zones if z.kind == kind and z.base_start == start and z.base_end == end]
    assert found, f"expected a {kind} zone with base ({start}, {end})"
    return found[0]


# ------------------------------------------------------------------- demand

def test_demand_zone_lines_label_and_confirm_index():
    candles = _demand_fixture()
    foundation = compute_foundation(candles, k=3)
    zones = detect_zones(candles, foundation, params)
    zone = pick(zones, 25, 26, DEMAND)
    base = candles[25:27]
    assert zone.formation == "RBR"
    assert zone.proximal == max(max(c.open, c.close) for c in base)
    assert zone.distal == min(c.low for c in base)
    assert zone.lower == zone.distal and zone.upper == zone.proximal
    assert zone.base_candles == 2
    assert zone.known_index == expected_confirmation(candles, foundation, 25, 26, DEMAND)
    atr_e = foundation.atr_at(26)
    assert zone.departure_atr >= float(params.supply_demand.leg_out_atr)
    assert zone.width <= float(params.supply_demand.max_zone_width_atr) * atr_e
    assert zone.fresh and zone.touches == []


def test_demand_zone_is_not_knowable_before_the_confirming_close():
    candles = _demand_fixture()
    foundation = compute_foundation(candles, k=3)
    confirm = expected_confirmation(candles, foundation, 25, 26, DEMAND)
    early = detect_zones(candles[:confirm], compute_foundation(candles[:confirm], k=3), params)
    assert not [z for z in early if z.kind == DEMAND and z.base_start == 25]
    zones = detect_zones(candles, foundation, params)
    assert pick(zones, 25, 26, DEMAND).known_index == confirm


def test_zone_goes_stale_on_the_first_return():
    candles = _demand_fixture([candle(31, 114.0, 114.5, 107.5, 110.0)])
    foundation = compute_foundation(candles, k=3)
    zones = detect_zones(candles, foundation, params)
    zone = pick(zones, 25, 26, DEMAND)
    assert not zone.fresh
    assert zone.touches == [31]
    assert zone.invalidated_index is None


def test_zone_invalidated_on_close_beyond_distal():
    candles = _demand_fixture([
        candle(31, 114.0, 114.5, 111.0, 112.0),
        candle(32, 112.0, 112.2, 106.5, 106.8),
    ])
    foundation = compute_foundation(candles, k=3)
    zones = detect_zones(candles, foundation, params)
    zone = pick(zones, 25, 26, DEMAND)
    buffer = float(params.supply_demand.invalidation_buffer_atr) * foundation.atr_at(32)
    assert candles[32].close < zone.distal - buffer
    assert zone.invalidated_index == 32


# ------------------------------------------------------------------- supply

def test_supply_zone_lines_label_and_confirm_index():
    candles = _supply_fixture()
    foundation = compute_foundation(candles, k=3)
    zones = detect_zones(candles, foundation, params)
    zone = pick(zones, 25, 26, SUPPLY)
    base = candles[25:27]
    assert zone.formation == "DBD"
    assert zone.proximal == min(min(c.open, c.close) for c in base)
    assert zone.distal == max(c.high for c in base)
    assert zone.upper == zone.distal and zone.lower == zone.proximal
    assert zone.known_index == expected_confirmation(candles, foundation, 25, 26, SUPPLY)


def test_supply_zone_invalidated_on_close_beyond_distal():
    candles = _supply_fixture([
        candle(31, 86.0, 87.5, 85.8, 87.0),
        candle(32, 87.0, 94.2, 86.9, 93.9),
    ])
    foundation = compute_foundation(candles, k=3)
    zones = detect_zones(candles, foundation, params)
    zone = pick(zones, 25, 26, SUPPLY)
    buffer = float(params.supply_demand.invalidation_buffer_atr) * foundation.atr_at(32)
    assert candles[32].close > zone.distal + buffer
    assert zone.invalidated_index == 32


# ------------------------------------------------------------------ filters

def test_zone_wider_than_the_limit_is_rejected():
    """A small-body candle with huge wicks would make a zone wider than 1.5 x ATR."""
    candles = warmup()
    closes = [101.5, 103.0, 104.5, 106.0, 107.0]
    open_ = 100.0
    for i, close in enumerate(closes):
        candles.append(candle(20 + i, open_, close + 0.2, open_ - 0.2, close))
        open_ = close
    candles += [
        candle(25, 107.0, 112.0, 104.0, 107.2),   # body 0.2, range 8: too wide
        candle(26, 107.2, 109.0, 106.8, 108.8),
        candle(27, 108.8, 111.0, 108.6, 110.5),
        candle(28, 110.5, 113.5, 110.3, 113.0),
        candle(29, 113.0, 116.0, 112.8, 115.5),
    ]
    foundation = compute_foundation(candles, k=3)
    zones = detect_zones(candles, foundation, params)
    assert not [z for z in zones if z.base_start <= 25 <= z.base_end]


def test_departure_without_a_leg_in_is_not_a_zone():
    candles = [candle(i, 100, 100.5, 99.5, 100) for i in range(20)]
    candles += [
        candle(20, 100, 100.3, 99.7, 100.2),      # base, but no leg in before it
        candle(21, 100.2, 100.4, 99.9, 100.1),
        candle(22, 100.1, 103.5, 100.0, 103.3),
        candle(23, 103.3, 106.5, 103.2, 106.3),
        candle(24, 106.3, 109.5, 106.2, 109.3),
    ]
    foundation = compute_foundation(candles, k=3)
    zones = detect_zones(candles, foundation, params)
    assert not zones


# ------------------------------------------------------------- entry rule v1

def test_first_touch_candidate_uses_the_proximal_line():
    candles = _demand_fixture([candle(31, 114.0, 114.5, 107.5, 110.0)])
    foundation = compute_foundation(candles, k=3)
    zones = detect_zones(candles, foundation, params)
    candidates = first_touch_candidates(zones, candles)
    assert candidates
    row = [r for r in candidates if r["zone"].base_start == 25][0]
    assert row["limit_price"] == row["zone"].proximal
    assert row["stop_price"] == row["zone"].distal
    assert row["entry_index"] == 31
    assert row["known_index"] == row["zone"].known_index < row["entry_index"]


# -------------------------------------------------------------- look-ahead

def test_no_lookahead_zones_match_prefix_runs():
    import random

    rng = random.Random(17)

    def noise(n: int, price: float) -> tuple[list[Candle], float]:
        out = []
        for _ in range(n):
            price += rng.gauss(0, 1.0)
            o = price - rng.gauss(0, 0.4)
            pad = abs(rng.gauss(0, 0.6)) + 0.2
            out.append(candle(0, o, max(o, price) + pad, min(o, price) - pad, price))
        return out, price

    candles: list[Candle] = []
    price = 100.0
    for _ in range(4):
        block, price = noise(30, price)
        candles.extend(block)
        o = price
        for _ in range(5):                      # leg in
            o = o + 1.3
            candles.append(candle(0, o - 1.3, o + 0.1, o - 1.4, o))
        for _ in range(3):                      # base
            candles.append(candle(0, o, o + 0.12, o - 0.12, o + 0.05))
        for _ in range(4):                      # leg out
            o = o + 1.3
            candles.append(candle(0, o - 1.3, o + 0.1, o - 1.4, o))
        price = o
    tail, price = noise(40, price)
    candles.extend(tail)
    for i, c in enumerate(candles):
        candles[i] = candle(i, c.open, c.high, c.low, c.close)

    foundation = compute_foundation(candles, k=3)
    baseline = detect_zones(candles, foundation, params)
    assert len(baseline) >= 1, "the injected base and legs should produce zones"

    for t in range(60, len(candles), 13):
        stopped = detect_zones(candles, foundation, params, stop_index=t)
        prefix = detect_zones(candles[: t + 1], compute_foundation(candles[: t + 1], k=3), params)
        key = lambda z: (z.kind, z.formation, round(z.proximal, 6), round(z.distal, 6),
                         z.base_start, z.base_end, z.known_index, len(z.touches),
                         z.invalidated_index)
        assert sorted(map(key, prefix)) == sorted(map(key, stopped)), f"zones differ at t={t}"
