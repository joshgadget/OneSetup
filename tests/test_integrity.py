from datetime import datetime, timedelta, timezone

from engine.data.integrity import drop_unfinished, find_gaps, is_stale, validate_series
from engine.models import Candle, MarketClass, Timeframe


def candle(t: datetime, symbol: str = "GBPUSD", tf: Timeframe = Timeframe.H1) -> Candle:
    return Candle(symbol=symbol, timeframe=tf, open_time=t, open=1.0, high=1.1, low=0.9, close=1.05)


def test_drop_unfinished():
    now = datetime(2026, 1, 5, 10, 30, tzinfo=timezone.utc)
    finished = candle(datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc))
    forming = candle(datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc))
    kept = drop_unfinished([finished, forming], Timeframe.H1, now=now)
    assert kept == [finished]


def test_weekend_gap_classified_for_forex():
    fri = candle(datetime(2026, 1, 2, 21, 0, tzinfo=timezone.utc))
    sun = candle(datetime(2026, 1, 4, 22, 0, tzinfo=timezone.utc))
    gaps = find_gaps([fri, sun], Timeframe.H1, MarketClass.FOREX)
    assert len(gaps) == 1 and gaps[0].kind == "weekend"
    assert gaps[0].missing_candles == 48


def test_hole_is_unexpected_for_synthetic():
    t0 = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    a = candle(t0, symbol="R_75")
    b = candle(t0 + timedelta(minutes=45), symbol="R_75", tf=Timeframe.M15)
    gaps = find_gaps([a, b], Timeframe.M15, MarketClass.SYNTHETIC)
    assert len(gaps) == 1 and gaps[0].kind == "unexpected" and gaps[0].missing_candles == 2


def test_no_gap_when_contiguous():
    t0 = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    rows = [candle(t0 + timedelta(hours=i)) for i in range(4)]
    assert find_gaps(rows, Timeframe.H1, MarketClass.FOREX) == []


def test_stale_and_validate_series():
    now = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    old = candle(datetime(2026, 1, 5, 5, 0, tzinfo=timezone.utc))
    assert is_stale(old, Timeframe.H1, now=now) is True
    fresh = candle(datetime(2026, 1, 5, 11, 0, tzinfo=timezone.utc))
    assert is_stale(fresh, Timeframe.H1, now=now) is False
    problems = validate_series([fresh, old])
    assert any("out-of-order" in p for p in problems)
    assert validate_series([fresh, fresh]) != []