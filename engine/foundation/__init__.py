"""Common foundation: candle maths, Wilder ATR, confirmed pivots, structure state.

The spec says to run this once per symbol/timeframe and let every module read it, so
`compute_foundation` is the single entry point. Everything it returns is aligned with
the candle list: index i of `atr` belongs to candle i.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from engine.foundation.atr import DEFAULT_ATR_PERIOD, atr_series, bodies, lower_wicks, ranges, true_ranges, upper_wicks, wilder_atr
from engine.foundation.pivots import DEFAULT_K, Pivot, confirmed_pivots, last_pivot, pivots_known_by
from engine.foundation.structure import ProtectedLevel, SwingLabel, Trend, label_swings, latest_label, protected_level, trend_at, trend_series
from engine.models import Candle

__all__ = [
    "Foundation", "compute_foundation",
    "DEFAULT_ATR_PERIOD", "DEFAULT_K",
    "Pivot", "SwingLabel", "Trend", "ProtectedLevel",
    "atr_series", "wilder_atr", "true_ranges", "ranges", "bodies", "upper_wicks", "lower_wicks",
    "confirmed_pivots", "pivots_known_by", "last_pivot",
    "label_swings", "trend_at", "trend_series", "latest_label", "protected_level",
]


@dataclass(frozen=True)
class Foundation:
    """Everything the common foundation defines, computed once per series."""

    candles: Sequence[Candle]
    atr: list[float | None]
    pivots: list[Pivot]
    labels: list[SwingLabel]
    atr_period: int
    k: int

    def __len__(self) -> int:
        return len(self.candles)

    def atr_at(self, index: int) -> float | None:
        if 0 <= index < len(self.atr):
            return self.atr[index]
        return None

    def trend_at(self, index: int) -> Trend:
        return trend_at(self.labels, index)

    def protected_level(self, index: int, trend: Trend | None = None) -> ProtectedLevel | None:
        return protected_level(self.labels, index, trend if trend is not None else self.trend_at(index))

    def range_at(self, index: int) -> float:
        return self.candles[index].high - self.candles[index].low

    def body_at(self, index: int) -> float:
        c = self.candles[index]
        return abs(c.close - c.open)

    def distance_in_atr(self, price: float, index: int) -> float | None:
        """d(P, t) = |P - C_t| / ATR_t, the normalised distance every ranking rule uses."""
        atr = self.atr_at(index)
        if atr is None or atr <= 0:
            return None
        return abs(price - self.candles[index].close) / atr


def compute_foundation(
    candles: Sequence[Candle],
    *,
    k: int = DEFAULT_K,
    atr_period: int = DEFAULT_ATR_PERIOD,
) -> Foundation:
    atr = wilder_atr(candles, atr_period)
    pivots = confirmed_pivots(candles, k)
    labels = label_swings(pivots)
    return Foundation(candles=candles, atr=atr, pivots=pivots, labels=labels, atr_period=atr_period, k=k)