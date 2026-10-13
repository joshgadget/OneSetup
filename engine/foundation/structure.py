"""Market-structure state machine: HH, HL, LH, LL and bull / bear / none.

Spec (SMC section):
    From confirmed pivots, label each new swing high HH or LH against the previous
    swing high, and each new swing low HL or LL against the previous swing low.
    trend = bull  when the latest high is HH and the latest low is HL
            bear  when the latest high is LH and the latest low is LL
            none  otherwise

It is a state machine over confirmed pivots, not a one-pivot guess. Every label and
every trend read carries the index at which it became knowable, so a strategy asking
"what was the trend at candle t" gets the answer that was true *at t*, not later.

Tie handling (a convention, recorded in DECISIONS.md): an equal high is labelled LH
and an equal low is labelled LL, i.e. it must be a new extreme to count as HH/HL.
That matters most on the Step indices, whose prices sit on a fixed grid.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from engine.foundation.pivots import Pivot, PivotKind, pivots_known_by


class Trend(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    NONE = "none"


@dataclass(frozen=True)
class SwingLabel:
    kind: PivotKind
    label: str          # HH | HL | LH | LL
    pivot: Pivot

    @property
    def known_index(self) -> int:
        return self.pivot.known_index

    @property
    def price(self) -> float:
        return self.pivot.price

    @property
    def index(self) -> int:
        return self.pivot.index


def label_swings(pivots: Sequence[Pivot]) -> list[SwingLabel]:
    """Label each confirmed pivot against the previous pivot of the same kind."""
    ordered = sorted(pivots, key=lambda pv: (pv.index, pv.kind))
    out: list[SwingLabel] = []
    last_high: Pivot | None = None
    last_low: Pivot | None = None
    for pv in ordered:
        if pv.kind == "high":
            label = "HH" if (last_high is None or pv.price > last_high.price) else "LH"
            last_high = pv
        else:
            label = "HL" if (last_low is None or pv.price > last_low.price) else "LL"
            last_low = pv
        out.append(SwingLabel(pv.kind, label, pv))
    return out


def labels_known_by(labels: Iterable[SwingLabel], index: int) -> list[SwingLabel]:
    return [lb for lb in labels if lb.known_index <= index]


def latest_label(labels: Sequence[SwingLabel], kind: PivotKind, index: int) -> SwingLabel | None:
    known = [lb for lb in labels if lb.kind == kind and lb.known_index <= index]
    return max(known, key=lambda lb: (lb.index, lb.known_index)) if known else None


def trend_at(labels: Sequence[SwingLabel], index: int) -> Trend:
    """The trend state as it was knowable at candle `index`."""
    high = latest_label(labels, "high", index)
    low = latest_label(labels, "low", index)
    if high is None or low is None:
        return Trend.NONE
    if high.label == "HH" and low.label == "HL":
        return Trend.BULL
    if high.label == "LH" and low.label == "LL":
        return Trend.BEAR
    return Trend.NONE


def trend_series(labels: Sequence[SwingLabel], length: int) -> list[Trend]:
    """Trend at every candle index, for charting and for tests."""
    return [trend_at(labels, i) for i in range(length)]


@dataclass(frozen=True)
class ProtectedLevel:
    """The level a trend's continuation depends on.

    SMC: in a bull trend, the swing low that formed immediately before the latest HH.
    SLP: the swing high that formed before the latest lower low in a bear trend.
    """

    pivot: Pivot
    label: SwingLabel

    @property
    def price(self) -> float:
        return self.pivot.price

    @property
    def known_index(self) -> int:
        return self.pivot.known_index


def protected_level(labels: Sequence[SwingLabel], index: int, trend: Trend) -> ProtectedLevel | None:
    """The protected swing as knowable at `index`, or None when the trend is none."""
    known = labels_known_by(labels, index)
    if trend is Trend.BULL:
        trigger = latest_label(known, "high", index)
        if trigger is None or trigger.label != "HH":
            return None
        prior = [lb for lb in known if lb.kind == "low" and lb.index < trigger.index]
        if not prior:
            return None
        low = max(prior, key=lambda lb: (lb.index, lb.known_index))
        return ProtectedLevel(low.pivot, low)
    if trend is Trend.BEAR:
        trigger = latest_label(known, "low", index)
        if trigger is None or trigger.label != "LL":
            return None
        prior = [lb for lb in known if lb.kind == "high" and lb.index < trigger.index]
        if not prior:
            return None
        high = max(prior, key=lambda lb: (lb.index, lb.known_index))
        return ProtectedLevel(high.pivot, high)
    return None