"""Phase 5 tests (SLP): bias, the full sequence, POI qualification and overlap scoring."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.config import load_params
from engine.foundation import compute_foundation
from engine.models import Candle, Direction, Timeframe
from engine.modules.liquidity import build_pools, detect_grabs
from engine.modules.slp import (
    PoiCandidate, bias_known_at, collect_candidates, detect_slp, htf_bias_series, overlap,
    score_candidates, unmitigated,
)
from engine.modules.supply_demand import Zone

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
params = load_params()


def candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(symbol="TEST", timeframe=Timeframe.H1, open_time=T0 + timedelta(hours=i),
                  open=o, high=h, low=l, close=c)


def warmup(n: int = 20) -> list[Candle]:
    return [candle(i, 100, 101, 99, 100) for i in range(n)]


# ------------------------------------------------------------------- bias

def test_htf_bias_is_bull_then_killed_by_a_close_through_the_protected_low():
    candles = warmup()
    candles += [
        candle(20, 100.0, 100.5, 96.0, 97.0),    # protected low 96, confirmed at 23
        candle(21, 97.0, 99.0, 96.9, 98.5),
        candle(22, 98.5, 100.0, 98.4, 99.5),
        candle(23, 99.5, 101.0, 99.4, 100.5),
        candle(24, 100.5, 102.0, 100.4, 101.5),
        candle(25, 101.5, 102.6, 101.4, 102.2),
        candle(26, 102.2, 103.4, 102.1, 103.0),
        candle(27, 103.0, 104.0, 102.9, 103.6),
        candle(28, 103.6, 104.4, 103.5, 104.0),   # higher high 104.4, confirmed at 31
        candle(29, 104.0, 104.1, 103.0, 103.3),
        candle(30, 103.3, 103.5, 102.2, 102.6),
        candle(31, 102.6, 102.8, 101.5, 102.0),
        candle(32, 102.0, 102.2, 101.0, 101.4),
        candle(33, 101.4, 101.5, 95.2, 95.6),     # close through 96 - eps: bias ends
        candle(34, 95.6, 97.0, 95.0, 96.5),
        candle(35, 96.5, 98.0, 96.0, 97.5),
    ]
    foundation = compute_foundation(candles, k=3)
    bias = htf_bias_series(candles, foundation, params)
    assert bias[30] == 0, "no trend until both pivots are confirmed"
    assert bias[31] == 1 and bias[32] == 1
    protected = foundation.protected_level(31, foundation.trend_at(31))
    assert protected is not None and protected.price == 96.0
    assert candles[33].close < 96.0 - float(params.slp.protected_break_atr) * foundation.atr_at(33)
    assert bias[33] == 0 and bias[34] == 0
    # the bias is a higher-timeframe read: on the same series with the entry timeframe
    # being the same candles, the value known at candle i is the previous candle's close
    assert bias_known_at(candles[32], candles, bias) == 1
    assert bias_known_at(candles[34], candles, bias) == 0
    assert bias_known_at(candles[0], candles, bias) is None


# ------------------------------------------------------- scoring plumbing

def test_overlap_and_confluence_score_are_the_spec_formulas():
    a = PoiCandidate("fvg", 0.0, 10.0, 1, 2)
    b = PoiCandidate("order_block", 5.0, 15.0, 1, 2)
    c = PoiCandidate("msnr", 20.0, 21.0, 1, 2)
    assert abs(overlap(a, b) - 0.5) < 1e-12
    assert abs(overlap(b, a) - 0.5) < 1e-12
    assert overlap(a, c) == 0.0
    scored = dict((cand.type, score) for cand, score in score_candidates([a, b, c], params))
    w = float(params.slp.poi_weights.fvg)
    assert abs(scored["fvg"] - (w + w * 0.5)) < 1e-12
    assert abs(scored["order_block"] - (w + w * 0.5)) < 1e-12
    assert abs(scored["msnr"] - w) < 1e-12
    # a zone that agrees with the others outranks the loner
    assert scored["fvg"] > scored["msnr"]


def test_unmitigated_is_evaluated_at_the_candle_asked_about():
    class Thing:
        def __init__(self, touches, invalidated=None, state="active"):
            self.touch_indices = touches
            self.invalidated_index = invalidated
            self.state = state

    cand = PoiCandidate("fvg", 0.0, 1.0, 1, 2, {}, Thing([61]))
    assert unmitigated(cand, 61), "a touch on this very candle has not happened yet at its open"
    assert not unmitigated(cand, 62)
    fresh = PoiCandidate("fvg", 0.0, 1.0, 1, 2, {}, Thing([63]))
    assert unmitigated(fresh, 62), "touches after the candle asked about cannot matter"
    broken = PoiCandidate("fvg", 0.0, 1.0, 1, 2, {}, Thing([], invalidated=61))
    assert unmitigated(broken, 61) and not unmitigated(broken, 62)
    inactive = PoiCandidate("fvg", 0.0, 1.0, 1, 2, {}, Thing([], state="broken"))
    assert not unmitigated(inactive, 99)


def test_collect_candidates_filters_by_side_and_type():
    from engine.modules.smc import Fvg

    bull = Fvg("bull", 10.0, 11.0, 5, 0.4, "midpoint")
    bear = Fvg("bear", 12.0, 13.0, 5, 0.4, "midpoint")
    longs = collect_candidates(fvgs=[bull, bear], side="long")
    assert [c.type for c in longs] == ["fvg"] and longs[0].lower == 10.0
    assert longs[0].formed_index == 3 and longs[0].known_index == 5


# ------------------------------------------------------------- full chain

def _chain_series(retrace_close: float = 88.6, retrace_low: float = 88.0) -> list[Candle]:
    from test_smc import _entry_chain_series, candle as mk

    series = list(_entry_chain_series())
    series[62] = mk(62, 95.5, 95.7, retrace_low, retrace_close)
    return series


def _demand_zone(touches=None, invalidated=None) -> Zone:
    zone = Zone(kind="demand", formation="RBR", proximal=88.5, distal=87.5,
                lower=87.5, upper=88.5, base_start=57, base_end=58, base_candles=2,
                known_index=59, departure_atr=2.4, leg_in="rally")
    zone.touches = list(touches or [])
    zone.invalidated_index = invalidated
    return zone


def _run(series, *, bias_value: int = 1, zones=None, fvgs=None, blocks=None, stop_index=None):
    foundation = compute_foundation(series, k=3)
    pools = build_pools(series, foundation, params, stop_index=stop_index)
    grabs = detect_grabs(series, foundation, pools, params, stop_index=stop_index)
    bias = [bias_value] * len(series)
    overrides = {}
    if fvgs is not None:
        overrides["fvgs"] = fvgs
    if blocks is not None:
        overrides["order_blocks"] = blocks
    return detect_slp(series, foundation, params, htf_candles=series, bias_series=bias,
                      pools=pools, grabs=grabs,
                      sd_zones=zones if zones is not None else [_demand_zone()],
                      stop_index=stop_index, **overrides)


def test_slp_long_sequence_prices_entry_stop_and_target():
    series = _chain_series()
    signals = _run(series)
    assert len(signals) == 1
    signal = signals[0]
    foundation = compute_foundation(series, k=3)
    atr = foundation.atr_at(62)
    assert signal.direction is Direction.LONG
    assert signal.meta["grab"]["known_index"] == 57
    assert signal.meta["mss"]["index"] == 60
    assert signal.known_time == series[62].open_time
    assert signal.entry == 88.5, "entry is the proximal edge of the POI"
    assert abs(signal.stop - (86.8 - 0.1 * atr)) < 1e-9, (
        "stop beyond the farther of the POI distal edge and the swept extreme")
    assert signal.target > signal.entry
    assert signal.meta["poi"]["type"] == "supply_demand"
    assert signal.meta["entry_variant"] == "proximal"


def test_slp_needs_the_bias_the_discount_and_the_order():
    series = _chain_series()
    signals = _run(series)
    assert len(signals) == 1
    canonical = signals[0].meta["grab"]["known_index"]
    assert not _run(series, bias_value=-1), "a long needs bias +1 at the grab"
    # the same grab, now closing above the equilibrium: that chain is premium, not
    # discount. The fixture holds a second, shallower sweep of an earlier pool that stays
    # in discount, so the check is against the premium chain itself, not the whole run.
    premium = list(series)
    premium[canonical] = candle(canonical, 89.5, 92.0, 86.8, 92.0)
    premium_signals = _run(premium)
    assert all(s.meta["grab"]["known_index"] != canonical for s in premium_signals), (
        "the liquidity event must happen while price is in discount")


def test_slp_rule_two_reads_touches_at_the_evaluation_candle():
    series = _chain_series()
    # the demand zone is the only candidate, so the test is about rule 2 and nothing else
    only_zone = dict(fvgs=[], blocks=[])
    # touched at 61, before the retrace candle: no longer unmitigated when the setup is
    # evaluated, so nothing fires
    assert not _run(series, zones=[_demand_zone(touches=[61])], **only_zone)
    # touched at 63, after the entry candle: the setup at 62 is untouched and still valid
    late_touch = _run(series, zones=[_demand_zone(touches=[63])], **only_zone)
    assert len(late_touch) == 1 and late_touch[0].known_time == series[62].open_time


def test_slp_cancels_on_a_close_beyond_the_swept_extreme_and_expires():
    # a retrace that touches no zone, so the setup waits for an entry that may never come
    waiting = _chain_series(retrace_low=93.0, retrace_close=94.0)
    assert not _run(waiting), "no touch, no setup: the model waits"
    cancelled = waiting + [candle(63, 94.0, 94.2, 85.5, 86.0)]  # closes under the swept low
    assert not _run(cancelled), "a close beyond the swept extreme cancels the setup"

    never_returns = waiting + [candle(i, 95.0, 96.0, 93.0, 94.0) for i in range(63, 86)]
    assert not _run(never_returns), "the setup expires if no entry arrives in time"

    # and when the entry does arrive before any cancellation is known, it is reported
    later_entry = waiting + [candle(63, 94.0, 94.2, 93.0, 93.5),
                             candle(64, 93.5, 93.6, 89.8, 90.2)]
    signals = _run(later_entry)
    assert len(signals) == 1 and signals[0].known_time == later_entry[64].open_time


def test_slp_prefix_consistency():
    series = _chain_series()
    zone = _demand_zone()
    foundation = compute_foundation(series, k=3)
    bias = [1] * len(series)
    for t in range(58, len(series)):
        stopped = detect_slp(series, foundation, params, htf_candles=series, bias_series=bias,
                             pools=build_pools(series, foundation, params, stop_index=t),
                             grabs=detect_grabs(series, foundation,
                                                build_pools(series, foundation, params,
                                                            stop_index=t), params,
                                                stop_index=t),
                             sd_zones=[zone], stop_index=t)
        prefix = series[: t + 1]
        prefix_foundation = compute_foundation(prefix, k=3)
        prefix_pools = build_pools(prefix, prefix_foundation, params)
        prefix_grabs = detect_grabs(prefix, prefix_foundation, prefix_pools, params)
        prefixed = detect_slp(prefix, prefix_foundation, params, htf_candles=prefix,
                              bias_series=bias[: t + 1], pools=prefix_pools,
                              grabs=prefix_grabs, sd_zones=[zone])
        key = lambda s: (s.direction.value, round(s.entry, 6), round(s.stop, 6),
                         round(s.target, 6), s.known_time)
        assert sorted(map(key, stopped)) == sorted(map(key, prefixed)), f"slp differs at t={t}"