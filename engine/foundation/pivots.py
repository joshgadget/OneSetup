"""Confirmed pivots, exactly as the common foundation defines them.

    SH_t = 1  iff  H_t > max_{1<=j<=k} H_{t-j}  and  H_t >= max_{1<=j<=k} H_{t+j}

and the mirror for swing lows with the inequalities reversed. That tie rule (strict
against the left neighbours, non-strict against the right ones) is a convention; it is
fixed here and parameterised in config/params.v1.yaml.

No look-ahead: a pivot at index t is only knowable at the close of candle t + k.
Every Pivot carries `known_index` and `known_time`; downstream code filters on those,
never on `index`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Sequence

from engine.models import Candle

PivotKind = Literal["high", "low"]

DEFAULT_K = 3


@dataclass(frozen=True)
class Pivot:
    kind: PivotKind
    index: int
    known_index: int
    price: float
    time: datetime
    known_time: datetime

    @property
    def is_high(self) -> bool:
        return self.kind == "high"


def confirmed_pivots(candles: Sequence[Candle], k: int = DEFAULT_K) -> list[Pivot]:
    """All confirmed swing highs and lows, ordered by formation index."""
    n = len(candles)
    out: list[Pivot] = []
    if k < 1 or n < 2 * k + 1:
        return out
    for t in range(k, n - k):
        known_index = t + k
        highs_left = [candles[t - j].high for j in range(1, k + 1)]
        lows_left = [candles[t - j].low for j in range(1, k + 1)]
        highs_right = [candles[t + j].high for j in range(1, k + 1)]
        lows_right = [candles[t + j].low for j in range(1, k + 1)]
        h, l = candles[t].high, candles[t].low
        if h > max(highs_left) and h >= max(highs_right):
            out.append(Pivot("high", t, known_index, h, candles[t].open_time, candles[known_index].open_time))
        if l < min(lows_left) and l <= min(lows_right):
            out.append(Pivot("low", t, known_index, l, candles[t].open_time, candles[known_index].open_time))
    out.sort(key=lambda pv: (pv.index, pv.kind))
    return out


def pivots_known_by(pivots: Sequence[Pivot], index: int) -> list[Pivot]:
    """Only the pivots a strategy could have seen at candle `index`."""
    return [pv for pv in pivots if pv.known_index <= index]


def last_pivot(pivots: Sequence[Pivot], kind: PivotKind, index: int) -> Pivot | None:
    """Latest confirmed pivot of that kind known at `index`."""
    candidates = [pv for pv in pivots if pv.kind == kind and pv.known_index <= index]
    return max(candidates, key=lambda pv: (pv.index, pv.known_index)) if candidates else None