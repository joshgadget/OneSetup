"""Phase 4 tests (CRT): reference range, sweep and reclaim, targets and filters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.config import load_params
from engine.foundation import compute_foundation
from engine.models import Candle, Direction, Timeframe
from engine.modules.crt import detect_crt, detect_crt_signals

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
params = load_params()


def candle(i: int, o: float, h: float, l: float, c: float, tf=Timeframe.H4) -> Candle:
    return Candle(symbol="TEST", timeframe=tf, open_time=T0 + timedelta(hours=4 * i),
                  open=o, high=h, low=l, close=c)


def warmup(n: int = 16) -> list[Candle]:
    return [candle(i, 100, 101, 99, 100) for i in range(n)]


def test_bearish_sweep_and_reclaim_fills_the_targets():
    candles = warmup()
    candles.append(candle(16, 100, 110, 100, 105))     # reference R: [100, 110]
    candles.append(candle(17, 105, 112, 104.5, 106))   # sweeps to 112, closes back inside
    foundation = compute_foundation(candles, k=3)
    setups = detect_crt(candles, foundation, params)
    assert len([s for s in setups if s.direction == "bear"]) == 1
    setup = [s for s in setups if s.direction == "bear"][0]
    atr = foundation.atr_at(17)
    assert setup.reference_high == 110 and setup.reference_low == 100
    assert setup.reference_mid == 105
    assert setup.known_index == 17 and setup.pierce_index == 17
    assert setup.variant == "one_candle"
    assert setup.entry == 106
    assert abs(setup.stop - (112 + 0.1 * atr)) < 1e-9
    assert setup.target_1 == 100 and setup.target_0 == 105
    # the signal record carries the same numbers and the shared shape
    signals = detect_crt_signals(candles, foundation, params)
    bear = [s for s in signals if s.direction is Direction.SHORT]
    assert len(bear) == 1
    assert bear[0].entry == 106 and bear[0].target == 100
    assert bear[0].known_time == candles[17].open_time


def test_bullish_sweep_mirrors():
    candles = warmup()
    candles.append(candle(16, 110, 110, 100, 105))
    candles.append(candle(17, 105, 105.5, 98, 104))    # sweeps under 100, closes inside
    foundation = compute_foundation(candles, k=3)
    setups = [s for s in detect_crt(candles, foundation, params) if s.direction == "bull"]
    assert len(setups) == 1
    setup = setups[0]
    assert setup.entry == 104
    assert abs(setup.stop - (98 - 0.1 * foundation.atr_at(17))) < 1e-9
    assert setup.target_1 == 110


def test_reclaim_within_one_candle_is_knowable_at_the_reclaim():
    candles = warmup()
    candles.append(candle(16, 100, 110, 100, 105))
    candles.append(candle(17, 105, 112, 104.5, 111))   # closes beyond the range: no setup yet
    candles.append(candle(18, 111, 111.5, 104, 108))   # next candle closes back inside
    foundation = compute_foundation(candles, k=3)
    setups = detect_crt(candles, foundation, params)
    reclaim = [s for s in setups if s.variant == "reclaim_within_n"]
    assert len(reclaim) == 1
    setup = reclaim[0]
    assert setup.pierce_index == 17 and setup.known_index == 18
    assert setup.entry == 108
    assert abs(setup.stop - (112 + 0.1 * foundation.atr_at(18))) < 1e-9
    # nothing is knowable at the sweep candle itself
    early = detect_crt(candles[:18], compute_foundation(candles[:18], k=3), params)
    assert not early


def test_a_narrow_reference_range_is_skipped():
    candles = warmup()
    candles.append(candle(16, 100, 100.5, 100, 100.2))  # width 0.5, ATR about 2
    candles.append(candle(17, 100.2, 101.5, 100.1, 100.4))  # would sweep if not filtered
    foundation = compute_foundation(candles, k=3)
    assert not detect_crt(candles, foundation, params)