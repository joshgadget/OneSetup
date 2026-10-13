"""Aggregate lower-timeframe candles into a higher timeframe.

Used only when a venue does not publish a native higher timeframe (verified live:
Deribit rejects a 4 hour resolution on its chart-data endpoint). Buckets are built
from complete base-candle sets only: if the base series has a hole inside a bucket,
that bucket is dropped rather than guessed, and a bucket is only marked closed once
its close time has passed - higher-timeframe candles follow the same no-look-ahead
rule as every other candle.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

from engine.models import Candle, Timeframe, utcnow


def can_resample(source: Timeframe, target: Timeframe) -> bool:
    return target.seconds % source.seconds == 0 and target.seconds > source.seconds


def resample_candles(
    candles: Sequence[Candle],
    source: Timeframe,
    target: Timeframe,
    *,
    now: datetime | None = None,
    expected_members: int | None = None,
) -> list[Candle]:
    """Aggregate complete buckets only.

    `expected_members` exists for markets that do not trade every day: a forex week is
    five daily candles, not seven, so a weekly bucket on a session market is complete at
    five members. Buckets that do not have exactly the expected count (a holiday, a hole
    in the base series) are dropped rather than guessed.
    """
    if not can_resample(source, target):
        raise ValueError(f"cannot resample {source.value} into {target.value}")
    now = now or utcnow()
    ratio = int(expected_members) if expected_members else target.seconds // source.seconds
    buckets: dict[int, list[Candle]] = {}
    for candle in sorted(candles, key=lambda c: c.open_time):
        bucket = int(candle.open_time.timestamp()) // target.seconds * target.seconds
        buckets.setdefault(bucket, []).append(candle)

    out: list[Candle] = []
    for bucket_start in sorted(buckets):
        members = buckets[bucket_start]
        if len(members) != ratio:
            continue  # incomplete bucket (hole in the base series): drop, do not guess
        start_dt = datetime.fromtimestamp(bucket_start, tz=timezone.utc)
        close_dt = start_dt + timedelta(seconds=target.seconds)
        real_volumes = [c.real_volume for c in members if c.real_volume is not None]
        tick_volumes = [c.tick_volume for c in members if c.tick_volume is not None]
        out.append(
            Candle(
                symbol=members[0].symbol,
                timeframe=target,
                open_time=start_dt,
                open=members[0].open,
                high=max(c.high for c in members),
                low=min(c.low for c in members),
                close=members[-1].close,
                tick_volume=sum(tick_volumes) if tick_volumes else None,
                real_volume=sum(real_volumes) if real_volumes else None,
                is_closed=close_dt <= now,
                source=f"{members[0].source}+resample",
            )
        )
    return out