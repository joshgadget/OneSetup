"""Shared helpers for strategy modules.

Every module returns the same record, so this is where that record is built: it fills
in the parameter version and hash so any stored signal can be traced back to the exact
parameter set that produced it, and it refuses to build a signal whose known_time is
before its formation_time.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from engine.config import load_params
from engine.models import Candle, Direction, EventStatus, ModuleSignal, Timeframe


def params_stamp() -> tuple[str, str]:
    params = load_params()
    return params.version, params.file_hash


def make_signal(
    *,
    module: str,
    version: str,
    symbol: str,
    timeframe: Timeframe,
    direction: Direction | None,
    entry: float,
    stop: float,
    target: float,
    candle: Candle,
    known_index: int,
    candles: list[Candle],
    status: EventStatus = EventStatus.CONFIRMED,
    score: float | None = None,
    probability: float | None = None,
    reasons: list[str] | None = None,
    invalidation: list[str] | None = None,
    features: dict[str, float] | None = None,
    meta: dict[str, Any] | None = None,
    formation_index: int | None = None,
) -> ModuleSignal:
    """Build the shared module record.

    `candle` is the candle whose close made the event knowable; `known_index` is its
    index in `candles`. Nothing here reads a later candle.
    """
    params_version, params_sha256 = params_stamp()
    formed = candles[formation_index] if formation_index is not None else candle
    return ModuleSignal(
        module_name=module,
        module_version=version,
        direction=direction or Direction.LONG,
        entry=float(entry),
        stop=float(stop),
        target=float(target),
        formation_time=formed.open_time,
        known_time=candle.open_time,
        status=status,
        params_version=params_version,
        params_sha256=params_sha256,
        symbol=symbol,
        timeframe=timeframe,
        score=score,
        probability=probability,
        reasons=list(reasons or []),
        invalidation=list(invalidation or []),
        features=dict(features or {}),
        meta=dict(meta or {}),
    )