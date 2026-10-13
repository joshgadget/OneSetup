"""Phase 4 tests (SMC): structure events, displacement, FVG, order blocks, entry model.

Fixtures are hand-built candle lists. Where a value can be recomputed straight from the
specification inequalities the test does that instead of trusting the module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.config import load_params
from engine.foundation import compute_foundation
from engine.models import Candle, Timeframe
from engine.modules.liquidity import build_pools, detect_grabs
from engine.modules.smc import (
    detect_entry_model_v1, detect_fvgs, detect_mss, detect_order_blocks,
    detect_structure_events, displacement,
)

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
params = load_params()


def candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(symbol="TEST", timeframe=Timeframe.H1, open_time=T0 + timedelta(hours=i),
                  open=o, high=h, low=l, close=c)


def warmup(n: int = 20) -> list[Candle]:
    return [candle(i, 100, 101, 99, 100) for i in range(n)]


# ------------------------------------------------------------ displacement

def test_displacement_rule():
    atr = 2.0
    # bullish: body 2.0 >= 1.0 x ATR, close ratio (104 - 102)/3 = 0.67: too low
    assert displacement(candle(0, 100, 105, 102, 104), atr, 1.0, 0.75) is None
    # same candle but closing at 104.5: ratio (104.5 - 102)/3 = 0.83
    assert displacement(candle(0, 100, 105, 102, 104.5), atr, 1.0, 0.75)[0] == "bull"
    # bearish mirror
    assert displacement(candle(0, 104, 105, 100.5, 101.0), atr, 1.0, 0.75)[0] == "bear"
    # body below alpha
    assert displacement(candle(0, 100, 103, 100, 101.5), atr, 1.0, 0.75) is None
    # zero range is invalid, never a division
    assert displacement(candle(0, 100, 100, 100, 100), atr, 1.0, 0.75) is None


# -------------------------------------------------------- structure events

def _structure_series() -> list[Candle]:
    """A bull leg with a BOS, then a bearish close with displacement through the
    protected low, then one more close below it (used to test the CHoCH suppression)."""
    candles = warmup()
    candles += [
        candle(20, 100.0, 101.5, 99.3, 99.6),    # swing high, confirmed at 23
        candle(21, 99.6, 100.1, 98.3, 98.6),
        candle(22, 98.6, 99.2, 97.0, 97.4),
        candle(23, 97.4, 98.4, 95.9, 96.4),
        candle(24, 96.4, 96.5, 94.4, 95.6),      # swing low 94.4, confirmed at 27
        candle(25, 95.6, 96.8, 94.6, 96.6),
        candle(26, 96.6, 97.8, 95.7, 97.6),
        candle(27, 97.6, 98.8, 96.7, 98.6),
        candle(28, 98.6, 99.8, 97.7, 99.6),
        candle(29, 99.6, 100.8, 98.7, 100.6),
        candle(30, 100.6, 101.8, 99.7, 101.6),
        candle(31, 101.6, 102.8, 100.7, 102.6),
        candle(32, 102.6, 103.8, 101.7, 103.6),
        candle(33, 103.6, 104.9, 103.0, 104.6),
        candle(34, 104.6, 105.7, 104.4, 105.5),  # swing high 105.7, confirmed at 37
        candle(35, 105.5, 105.2, 104.6, 105.0),
        candle(36, 105.0, 105.1, 104.2, 104.5),
        candle(37, 104.5, 104.7, 103.8, 104.0),
        candle(38, 104.0, 104.1, 101.8, 102.0),
        candle(39, 102.0, 102.1, 99.8, 100.0),
        candle(40, 100.0, 100.1, 97.8, 98.0),
        candle(41, 98.0, 98.2, 95.8, 96.0),
        candle(42, 96.0, 96.3, 92.9, 93.0),      # CHoCH: close under 94.4 - eps
        candle(43, 93.0, 93.2, 91.8, 92.0),      # another close below: must NOT be a CHoCH
    ]
    return candles


def test_bos_fires_on_the_first_close_beyond_the_last_known_swing_high():
    candles = _structure_series()
    foundation = compute_foundation(candles, k=3)
    events = detect_structure_events(candles, foundation, params)
    bos = [e for e in events if e.kind == "BOS"]
    assert bos and bos[0].direction == "bull"
    eps_atr = float(params.smc.bos_buffer_atr)
    expected = None
    for t in range(20, len(candles)):
        atr = foundation.atr_at(t)
        if atr is None or atr <= 0:
            continue
        if foundation.trend_at(t).value != "bull":
            continue
        pivots = [pv for pv in foundation.pivots
                  if pv.kind == "high" and pv.known_index <= t - 1]
        if not pivots:
            continue
        level = max(pivots, key=lambda pv: (pv.index, pv.known_index)).price
        if candles[t].close > level + eps_atr * atr:
            expected = t
            break
    assert bos[0].index == expected
    assert bos[0].level == 101.5, "the last swing high known at the time is the level"
    assert bos[0].known_index == bos[0].index


def test_choch_flips_the_trend_and_does_not_repeat_until_new_pivots():
    candles = _structure_series()
    foundation = compute_foundation(candles, k=3)
    events = detect_structure_events(candles, foundation, params)
    choch = [e for e in events if e.kind == "CHoCH"]
    assert len(choch) == 1, "the suppression after a CHoCH stops the second close below"
    assert choch[0].direction == "bear"
    assert choch[0].index == 42
    assert choch[0].level == 94.4
    choch_42 = next(e for e in events if e.kind == "CHoCH")
    assert candles[42].close < choch_42.level - float(params.smc.bos_buffer_atr) * foundation.atr_at(42)


def test_mss_needs_the_choch_candle_to_be_a_displacement_candle():
    candles = _structure_series()
    foundation = compute_foundation(candles, k=3)
    mss = detect_mss(candles, foundation, params)
    assert mss and mss[0].direction == "bear" and mss[0].index == 42
    assert mss[0].displacement_body_atr >= float(params.smc.displacement_body_atr)
    # the same series cut before the displacement candle yields no MSS
    early = detect_mss(candles[:41], compute_foundation(candles[:41], k=3), params)
    assert not early


def test_mss_grab_option_only_fires_with_a_grab():
    from conftest import params_with

    candles = _structure_series()
    foundation = compute_foundation(candles, k=3)
    strict = params_with(**{"smc.mss_require_liquidity_grab": True})
    events = detect_structure_events(candles, foundation, strict)
    assert not detect_mss(candles, foundation, strict, events=events, grabs=[]), (
        "with the option on and no grabs, nothing qualifies")
    assert detect_mss(candles, foundation, params, events=events), (
        "the shipped default does not require a grab; the option is a parameter")


# ------------------------------------------------------------------- FVG

def test_fvg_formula_width_filter_and_the_three_mitigation_variants():
    candles = warmup()
    candles += [
        candle(20, 100.0, 100.7, 99.8, 100.5),
        candle(21, 100.5, 101.7, 100.2, 101.5),   # t-2 for the bullish gap
        candle(22, 101.5, 102.1, 100.5, 102.0),
        candle(23, 102.0, 104.1, 102.5, 104.0),   # L_23 = 102.5 > H_21 = 101.7
        candle(24, 104.0, 104.2, 102.0, 103.5),   # touch and midpoint at once
        candle(25, 103.5, 103.6, 101.8, 103.0),
        candle(26, 103.0, 103.1, 101.5, 102.0),   # full
        candle(27, 102.0, 102.6, 101.9, 102.4),
        candle(28, 102.4, 103.0, 102.2, 102.6),
        candle(29, 102.9, 103.1, 102.6, 103.0),
        candle(30, 103.0, 103.2, 103.05, 103.1),  # narrow gap: 0.05 wide, dropped
    ]
    foundation = compute_foundation(candles, k=3)
    fvgs = detect_fvgs(candles, foundation, params)
    bull = [f for f in fvgs if f.direction == "bull"]
    assert len(bull) == 1, "the narrow gap at candle 30 must be dropped"
    fvg = bull[0]
    assert fvg.lower == 101.7 and fvg.upper == 102.5
    assert fvg.index == 23 and fvg.known_index == 23
    assert fvg.width >= float(params.smc.fvg_min_width_atr) * foundation.atr_at(23)
    assert fvg.first_touch_index == 24
    assert fvg.midpoint_index == 24
    assert fvg.full_index == 26
    assert fvg.variant == "midpoint"
    assert fvg.mitigated_index == 24
    assert fvg.touch_indices[:3] == [24, 25, 26]
    assert fvg.touch_indices == [24, 25, 26, 27, 28], (
        "every candle whose range intersected the zone counts as a touch")
    # the zone is knowable at 23 and never before
    assert not detect_fvgs(candles[:23], compute_foundation(candles[:23], k=3), params)


def test_bearish_fvg_mirrors():
    candles = warmup()
    candles += [
        candle(20, 100.0, 100.2, 99.3, 99.5),
        candle(21, 99.5, 99.6, 97.9, 98.0),       # t-2: L_21 = 97.9
        candle(22, 98.0, 98.1, 97.0, 97.2),
        candle(23, 97.2, 97.5, 96.0, 96.2),       # H_23 = 97.5 < L_21 = 97.9
        candle(24, 96.2, 98.2, 96.1, 98.0),
    ]
    foundation = compute_foundation(candles, k=3)
    fvgs = detect_fvgs(candles, foundation, params)
    bear = [f for f in fvgs if f.direction == "bear" and f.index == 23]
    assert bear, "the three-candle bearish gap must be registered"
    fvg = bear[0]
    assert fvg.lower == 97.5 and fvg.upper == 97.9
    assert fvg.first_touch_index == 24


# ---------------------------------------------------------- order blocks

def test_order_block_picks_the_latest_bearish_candle_and_requires_a_bos():
    candles = warmup()
    candles += [
        candle(20, 100.0, 101.2, 99.9, 101.0),
        candle(21, 101.0, 102.2, 100.9, 102.0),
        candle(22, 102.0, 104.6, 101.9, 104.0),   # swing high 104.6, confirmed at 25
        candle(23, 104.0, 104.2, 103.0, 103.5),   # bearish but not the latest
        candle(24, 103.5, 104.0, 103.2, 103.8),
        candle(25, 103.8, 103.9, 103.1, 103.4),   # the latest bearish candle before d
        candle(26, 103.4, 103.8, 103.2, 103.6),
        candle(27, 103.6, 106.7, 103.5, 106.5),   # displacement candle d, also a BOS
    ]
    foundation = compute_foundation(candles, k=3)
    blocks = detect_order_blocks(candles, foundation, params)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.index == 27 and block.known_index == 27
    assert block.candle_index == 25
    assert block.lower == candles[25].low and block.upper == candles[25].high
    assert block.is_bos and block.swing_broken == 104.6
    assert block.variant == "full_range"


# ------------------------------------------------------- entry model v1

def _entry_chain_series(retrace: bool = True) -> list[Candle]:
    """Bear structure, a sell-side grab of the external low, a bullish MSS, an FVG in
    the leg, then (optionally) the retrace into the gap."""
    candles = warmup()
    candles += [
        candle(20, 100.0, 101.5, 99.2, 99.5),
        candle(21, 99.5, 100.0, 98.2, 98.5),
        candle(22, 98.5, 99.0, 97.0, 97.2),
        candle(23, 97.2, 97.8, 95.8, 96.0),
        candle(24, 96.0, 96.2, 94.4, 94.6),
        candle(25, 94.6, 96.2, 94.5, 96.0),
        candle(26, 96.0, 97.7, 95.9, 97.5),
        candle(27, 97.5, 99.2, 97.4, 99.0),
        candle(28, 99.0, 100.7, 98.9, 100.5),
        candle(29, 100.5, 102.2, 100.4, 102.0),
        candle(30, 102.0, 103.7, 101.9, 103.5),
        candle(31, 103.5, 105.2, 103.4, 105.0),
        candle(32, 105.0, 106.2, 104.9, 106.0),
        candle(33, 106.0, 107.4, 105.9, 107.0),
        candle(34, 107.0, 114.4, 106.9, 113.5),
        candle(35, 113.5, 113.7, 110.8, 111.0),
        candle(36, 111.0, 111.1, 108.3, 108.5),
        candle(37, 108.5, 108.6, 105.8, 106.0),
        candle(38, 106.0, 106.3, 98.8, 99.0),
        candle(39, 99.0, 99.2, 96.8, 97.0),
        candle(40, 97.0, 97.2, 94.8, 95.0),
        candle(41, 95.0, 95.2, 92.8, 93.0),
        candle(42, 93.0, 93.3, 91.6, 92.0),
        candle(43, 92.0, 92.4, 90.6, 91.0),
        candle(44, 91.0, 91.2, 89.9, 90.2),
        candle(45, 90.2, 90.4, 88.4, 88.6),       # swing low 88.4
        candle(46, 88.6, 90.7, 88.5, 90.5),
        candle(47, 90.5, 92.2, 90.4, 92.0),
        candle(48, 92.0, 93.7, 91.9, 93.5),
        candle(49, 93.5, 94.8, 93.4, 94.5),       # swing high 94.8: lower high
        candle(50, 94.5, 94.6, 92.9, 93.0),
        candle(51, 93.0, 93.1, 90.8, 91.0),
        candle(52, 91.0, 91.2, 89.0, 89.5),
        candle(53, 89.5, 89.6, 87.4, 87.5),       # lower low 87.4
        candle(54, 87.5, 88.7, 87.6, 88.5),
        candle(55, 88.5, 89.2, 88.2, 89.0),
        candle(56, 89.0, 89.7, 89.0, 89.5),
        candle(57, 89.5, 89.6, 86.8, 88.5),       # sell-side grab of 87.4
        candle(58, 88.5, 91.2, 88.3, 91.0),
        candle(59, 91.0, 93.7, 90.9, 93.5),
        candle(60, 93.5, 97.7, 93.4, 97.5),       # bullish MSS with an FVG at 58..60
        candle(61, 97.5, 97.6, 95.2, 95.5),
    ]
    if retrace:
        candles.append(candle(62, 95.5, 95.7, 93.2, 94.0))   # retrace into the gap
    return candles


def test_entry_model_v1_enforces_the_order_and_prices_the_trade():
    candles = _entry_chain_series()
    foundation = compute_foundation(candles, k=3)
    pools = build_pools(candles, foundation, params)
    grabs = detect_grabs(candles, foundation, pools, params)
    signals = detect_entry_model_v1(candles, foundation, params, pools=pools, grabs=grabs)
    assert len(signals) == 1
    signal = signals[0]
    meta = signal.meta
    assert meta["grab"]["known_index"] == 57 < meta["mss"]["index"] == 60 < len(candles) - 1
    assert signal.known_time == candles[62].open_time
    assert signal.entry == 93.4, "entry is the proximal edge of the FVG"
    atr = foundation.atr_at(62)
    assert abs(signal.stop - (86.8 - 0.1 * atr)) < 1e-9, "stop below the swept low"
    assert signal.target > signal.entry
    assert signal.stop < signal.entry < signal.target


def test_entry_model_v1_is_not_knowable_before_the_retrace():
    candles = _entry_chain_series(retrace=False)
    foundation = compute_foundation(candles, k=3)
    pools = build_pools(candles, foundation, params)
    grabs = detect_grabs(candles, foundation, pools, params)
    assert not detect_entry_model_v1(candles, foundation, params, pools=pools, grabs=grabs)
    # and a prefix that stops short of the retrace candle also yields nothing
    short = _entry_chain_series()[:62]
    foundation_short = compute_foundation(short, k=3)
    pools_short = build_pools(short, foundation_short, params)
    grabs_short = detect_grabs(short, foundation_short, pools_short, params)
    assert not detect_entry_model_v1(short, foundation_short, params,
                                     pools=pools_short, grabs=grabs_short)


# ------------------------------------------------------------- look-ahead

def test_structure_and_fvg_prefix_consistency():
    import random

    rng = random.Random(5)
    price = 100.0
    candles = []
    for i in range(220):
        price += rng.gauss(0, 1.1)
        o = price - rng.gauss(0, 0.5)
        pad = abs(rng.gauss(0, 0.7)) + 0.2
        candles.append(candle(i, o, max(o, price) + pad, min(o, price) - pad, price))
    foundation = compute_foundation(candles, k=3)

    for t in range(60, len(candles), 17):
        stopped = detect_structure_events(candles, foundation, params, stop_index=t)
        prefix = detect_structure_events(candles[: t + 1],
                                         compute_foundation(candles[: t + 1], k=3), params)
        key = lambda e: (e.kind, e.direction, e.index, round(e.level, 6), e.level_index)
        assert sorted(map(key, prefix)) == sorted(map(key, stopped)), f"events differ at t={t}"

        stopped_f = detect_fvgs(candles, foundation, params, stop_index=t)
        prefix_f = detect_fvgs(candles[: t + 1], compute_foundation(candles[: t + 1], k=3), params)
        key_f = lambda f: (f.direction, round(f.lower, 6), round(f.upper, 6), f.index,
                           len(f.touch_indices), f.mitigated_index)
        assert sorted(map(key_f, prefix_f)) == sorted(map(key_f, stopped_f)), f"fvg differ at t={t}"