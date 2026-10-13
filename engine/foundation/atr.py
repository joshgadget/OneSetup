"""Wilder-smoothed ATR and the candle primitives of the spec's common foundation.

    R_t = H_t - L_t                        B_t = |C_t - O_t|
    W^u_t = H_t - max(O_t, C_t)            W^l_t = min(O_t, C_t) - L_t
    TR_t = max(H_t - L_t, |H_t - C_{t-1}|, |L_t - C_{t-1}|)
    ATR_t = ((n-1) ATR_{t-1} + TR_t) / n,  n = 14

Seeded with the simple average of the first n true ranges. ATR is None until that
seed exists; callers handle None rather than substituting a different measure.
"""
from __future__ import annotations

from typing import Sequence

from engine.models import Candle

DEFAULT_ATR_PERIOD = 14


def ranges(candles: Sequence[Candle]) -> list[float]:
    """R_t = H_t - L_t."""
    return [c.high - c.low for c in candles]


def bodies(candles: Sequence[Candle]) -> list[float]:
    """B_t = |C_t - O_t|."""
    return [abs(c.close - c.open) for c in candles]


def upper_wicks(candles: Sequence[Candle]) -> list[float]:
    """W^u_t = H_t - max(O_t, C_t)."""
    return [c.high - max(c.open, c.close) for c in candles]


def lower_wicks(candles: Sequence[Candle]) -> list[float]:
    """W^l_t = min(O_t, C_t) - L_t."""
    return [min(c.open, c.close) - c.low for c in candles]


def true_ranges(candles: Sequence[Candle]) -> list[float]:
    """TR_t, using the previous *available* close across breaks (D-005.2)."""
    out: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            out.append(c.high - c.low)
            continue
        prev_close = candles[i - 1].close
        out.append(max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close)))
    return out


def wilder_atr(candles: Sequence[Candle], period: int = DEFAULT_ATR_PERIOD) -> list[float | None]:
    """Wilder ATR aligned with `candles`; None before the seed is available."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period < 1 or n < period:
        return out
    trs = true_ranges(candles)
    value = sum(trs[:period]) / period
    out[period - 1] = value
    for t in range(period, n):
        value = ((period - 1) * value + trs[t]) / period
        out[t] = value
    return out


def atr_series(candles: Sequence[Candle], period: int = DEFAULT_ATR_PERIOD) -> list[float | None]:
    """The spec uses one ATR everywhere: Wilder smoothed, n = 14."""
    return wilder_atr(candles, period)