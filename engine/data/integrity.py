"""Candle-series integrity checks: unfinished candles, gaps, staleness.

The spec is explicit: never use an unfinished candle, including higher timeframe
candles, and never signal from a stale feed or a gap in the candle series. These
checks are applied at the adapter boundary and again in the store path.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from engine.models import Candle, MarketClass, Timeframe, utcnow


@dataclass(frozen=True)
class Gap:
    symbol: str
    timeframe: Timeframe
    prev_close: datetime      # close time of the last candle before the gap
    next_open: datetime       # open time of the first candle after the gap
    missing_candles: int
    kind: str                 # weekend | expected | unexpected

    def __str__(self) -> str:
        return (f"{self.symbol} {self.timeframe.value}: {self.missing_candles} missing candle(s) "
                f"between {self.prev_close.isoformat()} and {self.next_open.isoformat()} ({self.kind})")


def drop_unfinished(candles: Iterable[Candle], timeframe: Timeframe, now: datetime | None = None) -> list[Candle]:
    now = now or utcnow()
    horizon = timedelta(seconds=timeframe.seconds)
    return [c for c in candles if (c.open_time + horizon) <= now]


def only_closed(candles: Iterable[Candle]) -> list[Candle]:
    return [c for c in candles if c.is_closed]


def is_stale(last_candle: Candle, timeframe: Timeframe, now: datetime | None = None, multiple: int = 2) -> bool:
    """True when the newest candle's close is older than `multiple` timeframe lengths."""
    now = now or utcnow()
    age = now - last_candle.close_time
    return age > timedelta(seconds=timeframe.seconds * multiple)


def _is_weekend_span(start: datetime, end: datetime) -> bool:
    """A gap that covers Saturday, or starts Friday evening and ends Sunday evening."""
    cursor = start
    seen = set()
    while cursor <= end:
        seen.add(cursor.weekday())  # Mon=0 .. Sun=6
        cursor += timedelta(hours=6)
    return 5 in seen or 6 in seen


# Default session-break allowance per instrument family, in minutes. These cover the
# routine daily maintenance breaks (gold and metals pause ~1 hour, cash index CFDs
# pause between their sessions). Instrument-specific hours are still loaded from the
# provider where it publishes them; this is only the fallback.
_DEFAULT_BREAK_MINUTES: dict[MarketClass, int] = {
    MarketClass.GOLD: 60,
    MarketClass.COMMODITY: 60,
    MarketClass.INDEX: 60,
}


def default_break_minutes(market_class: MarketClass) -> int:
    return _DEFAULT_BREAK_MINUTES.get(market_class, 0)


def find_gaps(
    candles: Sequence[Candle],
    timeframe: Timeframe,
    market_class: MarketClass,
    *,
    allowed_break_minutes: int | None = None,
) -> list[Gap]:
    """Gaps between consecutive candles, classified.

    Session instruments (forex, gold, metals, index CFDs) close for weekends and
    short daily breaks, so those are "weekend" / covered by the break allowance.
    Continuous instruments (crypto, synthetic indices) trade 24/7, so any gap there
    is unexpected and blocks signals (spec: no signal from a gap in the series).
    """
    out: list[Gap] = []
    if len(candles) < 2:
        return out
    if allowed_break_minutes is None:
        allowed_break_minutes = default_break_minutes(market_class)
    step = timedelta(seconds=timeframe.seconds)
    tolerance = timedelta(minutes=allowed_break_minutes)
    for prev, nxt in zip(candles, candles[1:]):
        delta = nxt.open_time - prev.open_time
        if delta <= step + tolerance:
            continue
        missing = max(0, int((delta - tolerance) / step) - 1)
        if missing == 0:
            continue
        if market_class.has_sessions and _is_weekend_span(prev.close_time, nxt.open_time):
            kind = "weekend"
        elif market_class.trades_continuously:
            kind = "unexpected"
        else:
            kind = "unexpected"
        out.append(Gap(prev.symbol, timeframe, prev.close_time, nxt.open_time, missing, kind))
    return out


def validate_series(candles: Sequence[Candle]) -> list[str]:
    """Ordering and duplicate checks. Returns human-readable problems."""
    problems: list[str] = []
    seen: set[datetime] = set()
    last: datetime | None = None
    for c in candles:
        if last is not None and c.open_time <= last:
            problems.append(f"out-of-order candle at {c.open_time.isoformat()}")
        last = c.open_time
        if c.open_time in seen:
            problems.append(f"duplicate candle at {c.open_time.isoformat()}")
        seen.add(c.open_time)
    return problems