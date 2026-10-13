"""Phase 4 tests (ICT): sessions with daylight saving, OTE, Judas swing, power of three,
Silver Bullet and the discount gate of the entry model."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import params_with
from engine.config import load_params
from engine.foundation import compute_foundation
from engine.models import Candle, Timeframe
from engine.modules.ict import (
    dealing_range_eq, detect_entry_model_v1, detect_judas_swing, detect_power_of_three,
    detect_silver_bullet, in_session, ote_zone, session_blocks,
)
from engine.modules.liquidity import Grab, Side, build_pools, detect_grabs
from engine.modules.smc import Fvg

UTC = timezone.utc
params = load_params()


def candle_at(moment: datetime, o: float, h: float, l: float, c: float,
              tf: Timeframe = Timeframe.M15) -> Candle:
    return Candle(symbol="TEST", timeframe=tf, open_time=moment, open=o, high=h, low=l, close=c)


def flat(moment: datetime, tf: Timeframe = Timeframe.M15, price: float = 100.0, pad: float = 1.0) -> Candle:
    return candle_at(moment, price, price + pad, price - pad, price, tf=tf)


# --------------------------------------------------------------- sessions

def test_session_window_follows_new_york_daylight_saving():
    """06:30 UTC is 01:30 New York in winter and 02:30 in summer.

    The London window is 02:00-05:00 New York time, so the same UTC clock time is
    outside the window in January and inside it in July. A fixed Lagos (or UTC) hour
    would get this wrong twice a year, which is why the spec asks for the conversion.
    """
    winter = flat(datetime(2026, 1, 5, 6, 30, tzinfo=UTC))
    summer = flat(datetime(2026, 7, 6, 6, 30, tzinfo=UTC))
    london = params.ict.sessions["london"]
    assert not in_session(winter, london)
    assert in_session(summer, london)


def test_session_blocks_group_consecutive_candles():
    start = datetime(2026, 1, 5, 6, 0, tzinfo=UTC)       # 01:00 New York: outside
    candles = [flat(start + timedelta(minutes=15 * i)) for i in range(20)]
    blocks = session_blocks(candles, params, session="london")
    london = params.ict.sessions["london"]
    inside = [i for i, c in enumerate(candles) if in_session(c, london)]
    assert blocks and blocks[0] == (inside[0], inside[-1])


# ------------------------------------------------------- range, EQ and OTE

def test_ote_formula():
    low, high = ote_zone(100.0, 200.0)
    assert abs(low - 121.0) < 1e-12 and abs(high - 138.0) < 1e-12


def test_dealing_range_split_into_discount_and_premium():
    candles = [flat(datetime(2026, 1, 5, tzinfo=UTC) + timedelta(hours=i), Timeframe.H1) for i in range(20)]
    candles += [
        candle_at(candles[-1].open_time + timedelta(hours=1), 100.0, 101.0, 94.0, 95.0, Timeframe.H1),
        candle_at(candles[-1].open_time + timedelta(hours=2), 95.0, 96.0, 93.5, 94.5, Timeframe.H1),
        candle_at(candles[-1].open_time + timedelta(hours=3), 94.5, 98.0, 94.4, 97.0, Timeframe.H1),
        candle_at(candles[-1].open_time + timedelta(hours=4), 97.0, 104.0, 96.9, 103.0, Timeframe.H1),
        candle_at(candles[-1].open_time + timedelta(hours=5), 103.0, 104.5, 102.0, 103.5, Timeframe.H1),
        candle_at(candles[-1].open_time + timedelta(hours=6), 103.5, 104.0, 102.8, 103.0, Timeframe.H1),
        candle_at(candles[-1].open_time + timedelta(hours=7), 103.0, 103.6, 102.6, 102.8, Timeframe.H1),
        candle_at(candles[-1].open_time + timedelta(hours=8), 102.8, 103.0, 101.8, 102.2, Timeframe.H1),
        candle_at(candles[-1].open_time + timedelta(hours=9), 102.2, 102.6, 101.6, 102.0, Timeframe.H1),
    ]
    foundation = compute_foundation(candles, k=3)
    read = dealing_range_eq(foundation, len(candles) - 1)
    assert read is not None
    assert read["low"] == 93.5 and read["high"] == 104.5
    assert read["eq"] == 99.0
    assert read["zone"] == "premium"      # last close 102.0 above the midpoint


# ------------------------------------------------------------ Judas swing

def test_judas_swing_is_knowable_only_at_the_reclaim_close():
    base = datetime(2026, 1, 5, 6, 0, tzinfo=UTC)          # 01:00 New York
    candles = [flat(base - timedelta(hours=20 - i), Timeframe.H1) for i in range(20)]
    candles += [
        # session opens at 07:00 UTC (02:00 New York)
        candle_at(base + timedelta(hours=1), 100.0, 100.05, 99.8, 100.0, Timeframe.H1),
        candle_at(base + timedelta(hours=2), 100.0, 100.1, 98.5, 99.0, Timeframe.H1),
        candle_at(base + timedelta(hours=3), 99.0, 101.0, 98.9, 100.5, Timeframe.H1),
    ]
    foundation = compute_foundation(candles, k=3)
    events = detect_judas_swing(candles, foundation, params, session="london")
    assert len(events) == 1
    event = events[0]
    assert event.side == "sell_side"
    assert event.session_start == 20
    assert event.session_open == 100.0
    assert event.pierce_index == 20, "the excursion can start on the session's first candle"
    assert event.reclaim_index == 22
    assert event.known_index == 22
    assert event.depth_atr > 0
    # nothing is knowable at the piercing candle or the one after it
    early = detect_judas_swing(candles[:22], compute_foundation(candles[:22], k=3), params,
                               session="london")
    assert not early


# ------------------------------------------------------- power of three

def _po3_candles(accumulation_high: float, accumulation_low: float) -> list[Candle]:
    base = datetime(2026, 1, 5, 7, 0, tzinfo=UTC)          # 02:00 New York, London window
    candles = [flat(base - timedelta(minutes=15 * (20 - i))) for i in range(20)]
    for i, price in enumerate([100.0, 100.05, 100.0, 100.02]):    # accumulation: 4 candles
        candles.append(candle_at(base + timedelta(minutes=15 * i),
                                 price, accumulation_high, accumulation_low, price))
    candles.append(candle_at(base + timedelta(minutes=75), 99.5, 99.6, 99.0, 99.3))   # manipulation
    candles.append(candle_at(base + timedelta(minutes=90), 99.4, 99.8, 99.3, 99.6))
    candles.append(candle_at(base + timedelta(minutes=105), 99.6, 102.2, 99.5, 102.0))  # distribution
    return candles


def test_power_of_three_accumulation_manipulation_distribution():
    candles = _po3_candles(100.1, 99.9)
    foundation = compute_foundation(candles, k=3)
    events = detect_power_of_three(candles, foundation, params, session="london")
    assert len(events) == 1
    event = events[0]
    assert event.accumulation_start == 20 and event.accumulation_end == 23
    assert event.accumulation_high == 100.1 and event.accumulation_low == 99.9
    assert event.manipulation_index == 24
    assert event.distribution_index == 26
    assert event.direction == "bull"
    assert event.known_index == 26


def test_power_of_three_skips_a_wide_accumulation():
    candles = _po3_candles(101.5, 98.5)      # width 3 > 1 x ATR (about 2)
    foundation = compute_foundation(candles, k=3)
    assert not detect_power_of_three(candles, foundation, params, session="london")


# -------------------------------------------------------- Silver Bullet

def _silver_bullet_candles() -> list[Candle]:
    base = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)         # 10:00 New York (winter)
    return [candle_at(base + timedelta(minutes=15 * i), 100.0, 100.5, 99.5, 100.0)
            for i in range(8)]


def test_silver_bullet_needs_the_window_and_a_prior_sweep():
    candles = _silver_bullet_candles()
    foundation = compute_foundation(candles, k=3)
    fvg = Fvg("bull", 99.0, 100.0, 2, 0.5, "midpoint")
    inside = Grab(Side.SELL, 98.5, 0, 1, "one_candle", 0.4, 0.8, 1, 98.0, True)
    outside = Grab(Side.SELL, 98.5, 0, 1, "one_candle", 0.4, 0.8, 1, 98.0, True)
    hits = detect_silver_bullet(candles, foundation, params, fvgs=[fvg], grabs=[inside])
    assert len(hits) == 1
    assert hits[0].known_index == 2 and hits[0].direction == "bull"
    # a sweep that is too old
    far = Grab(Side.SELL, 98.5, 0, 0, "one_candle", 0.4, 0.8, 1, 98.0, True)
    old_window = params_with(**{"ict.sweep_window_candles": 1})
    assert not detect_silver_bullet(candles, foundation, old_window, fvgs=[fvg], grabs=[far])
    # a gap outside the window
    late = Fvg("bull", 99.0, 100.0, 6, 0.5, "midpoint")
    assert not detect_silver_bullet(candles, foundation, params, fvgs=[late], grabs=[inside])
    # a buy-side sweep does not support a bullish gap
    wrong_side = Grab(Side.BUY, 101.5, 0, 1, "one_candle", 0.4, 0.8, 1, 102.0, True)
    assert not detect_silver_bullet(candles, foundation, params, fvgs=[fvg], grabs=[wrong_side])
    assert outside is not None


# ----------------------------------------------------- entry model v1

def test_entry_model_v1_requires_discount_and_a_session():
    from test_smc import _entry_chain_series

    series = _entry_chain_series()
    foundation = compute_foundation(series, k=3)
    pools = build_pools(series, foundation, params)
    grabs = detect_grabs(series, foundation, pools, params)
    all_day = params_with(**{"ict.sessions.test_window":
                             {"start": "00:00", "end": "23:59", "tz": "UTC"}})
    blocked = detect_entry_model_v1(series, foundation, all_day, pools=pools, grabs=grabs,
                                    session_names_used=["test_window"])
    assert not blocked, "at the retrace the close sits above EQ, so the model is gated"

    # A version of the same chain where a swing high above the entry has been
    # confirmed before the retrace, so the close at the retrace is in discount.
    strong = _discount_chain_series()
    foundation = compute_foundation(strong, k=3)
    pools = build_pools(strong, foundation, params)
    grabs = detect_grabs(strong, foundation, pools, params)
    signals = detect_entry_model_v1(strong, foundation, all_day, pools=pools, grabs=grabs,
                                    session_names_used=["test_window"])
    assert len(signals) == 1
    signal = signals[0]
    assert signal.meta["range"]["zone"] == "discount"
    assert signal.meta["grab"]["known_index"] == 57
    assert signal.meta["mss"]["index"] == 60
    assert signal.entry == 93.4
    assert signal.known_time == strong[62].open_time
    assert signal.stop < signal.entry < signal.target


def _discount_chain_series():
    """The SMC chain with a deep retrace: price fills the gap but closes below EQ.

    EQ sits at (86.8 + 94.8) / 2 = 90.8 with the swings the chain has confirmed by the
    retrace candle, so a close at 90.2 is in discount while a close at 94.0 is not.
    """
    from test_smc import _entry_chain_series, candle

    series = list(_entry_chain_series())
    series[62] = candle(62, 95.5, 95.7, 90.0, 90.2)
    return series


def test_entry_model_v1_respects_the_session_gate():
    series = _discount_chain_series()
    foundation = compute_foundation(series, k=3)
    pools = build_pools(series, foundation, params)
    grabs = detect_grabs(series, foundation, pools, params)
    narrow = params_with(**{"ict.sessions.test_window":
                            {"start": "05:00", "end": "05:30", "tz": "UTC"}})
    assert not detect_entry_model_v1(series, foundation, narrow, pools=pools, grabs=grabs,
                                     session_names_used=["test_window"])