"""Outcome labelling: target before stop, with an expiry rule.

The spec labels every historical candidate 1 if the target came first and 0 if the stop
did, with an expiry rule for the rest. A candle is only known at its close, so when one
candle contains both the stop and the target the intrabar order is unknown: the label
takes the stop, because claiming the target came first would be look-ahead the data
cannot support (DECISIONS.md D-014).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from engine.models import Candle, Direction

TARGET = "target"
STOP = "stop"
EXPIRED = "expired"
UNFILLED = "unfilled"
NO_TARGET = "no_target"


@dataclass(frozen=True)
class Resolution:
    outcome: str
    exit_index: int
    exit_price: float
    r_gross: float
    bars_in_trade: int
    ambiguous: bool = False


def r_multiple(direction: Direction, entry: float, stop: float, exit_price: float) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        raise ValueError("entry and stop must differ")
    if direction is Direction.LONG:
        return (exit_price - entry) / risk
    return (entry - exit_price) / risk


def label_outcome(
    candles: Sequence[Candle],
    *,
    direction: Direction,
    entry: float,
    stop: float,
    target: float,
    fill_index: int,
    expiry_candles: int,
    exit_unresolved_at_market: bool = True,
) -> Resolution:
    """Walk forward from the fill candle and return the first touch of stop or target.

    The fill candle counts: the order filled inside it and the stop may have been hit
    inside the same candle, which is the ambiguous case and takes the stop.
    """
    if expiry_candles < 1:
        raise ValueError("expiry_candles must be at least 1")
    if fill_index >= len(candles):
        raise ValueError("fill_index is past the end of the series")
    last = min(fill_index + int(expiry_candles) - 1, len(candles) - 1)
    for j in range(fill_index, last + 1):
        candle = candles[j]
        if direction is Direction.LONG:
            hit_stop = candle.low <= stop
            hit_target = candle.high >= target
        else:
            hit_stop = candle.high >= stop
            hit_target = candle.low <= target
        if hit_stop:
            return Resolution(
                outcome=STOP, exit_index=j, exit_price=stop,
                r_gross=r_multiple(direction, entry, stop, stop),
                bars_in_trade=j - fill_index + 1, ambiguous=hit_target,
            )
        if hit_target:
            return Resolution(
                outcome=TARGET, exit_index=j, exit_price=target,
                r_gross=r_multiple(direction, entry, stop, target),
                bars_in_trade=j - fill_index + 1, ambiguous=False,
            )
    if not exit_unresolved_at_market:
        raise NotImplementedError("only the market exit at expiry is implemented")
    close = candles[last].close
    return Resolution(
        outcome=EXPIRED, exit_index=last, exit_price=close,
        r_gross=r_multiple(direction, entry, stop, close),
        bars_in_trade=last - fill_index + 1,
    )
