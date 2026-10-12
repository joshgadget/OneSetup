"""Core domain models shared by adapters, detectors and the API.

All timestamps are timezone-aware UTC. Every detected event (Phase 1+) records
formation_time and known_time: the spec's no-look-ahead rule says a pivot at t is
only knowable at the close of t + k, and a signal may only use information that was
available at its known_time. Candle rows carry an explicit is_closed flag so an
unfinished candle can never reach a detector.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MarketClass(str, Enum):
    FOREX = "forex"
    GOLD = "gold"
    CRYPTO_PERP = "crypto_perp"
    CRYPTO_FUTURES = "crypto_futures"
    SYNTHETIC = "synthetic"


_TF_SECONDS: dict[str, int] = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H2": 7200, "H3": 10800, "H4": 14400, "H6": 21600, "H8": 28800,
    "D1": 86400, "W1": 604800, "MN1": 2592000,
}
_TF_PANDAS: dict[str, str] = {
    "M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
    "H1": "1h", "H2": "2h", "H3": "3h", "H4": "4h", "H6": "6h", "H8": "8h",
    "D1": "1D", "W1": "1W", "MN1": "1MS",
}


class Timeframe(str, Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H2 = "H2"
    H3 = "H3"
    H4 = "H4"
    H6 = "H6"
    H8 = "H8"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"

    @property
    def seconds(self) -> int:
        return _TF_SECONDS[self.value]

    @property
    def pandas_freq(self) -> str:
        return _TF_PANDAS[self.value]

    @classmethod
    def parse(cls, value: str) -> "Timeframe":
        key = value.strip().upper()
        aliases = {"1M": "M1"}
        key = aliases.get(key, key)
        try:
            return cls(key)
        except ValueError as exc:
            raise ValueError(f"unknown timeframe {value!r}") from exc


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class EventStatus(str, Enum):
    FORMING = "forming"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware (UTC)")
    return value.astimezone(timezone.utc)


class Candle(BaseModel):
    """One completed candle of one timeframe, in UTC.

    Prices are the analysis series. For FXCM they are the mid of bid and ask by
    default (see DECISIONS.md D-002); the raw series is recoverable from the
    adapter if a different side is configured.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: Timeframe
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: float | None = None
    real_volume: float | None = None
    is_closed: bool = True
    source: str = ""

    @field_validator("open_time")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _ensure_utc(v)

    @model_validator(mode="after")
    def _sane_prices(self) -> "Candle":
        if self.high < self.low:
            raise ValueError(f"high {self.high} < low {self.low} at {self.open_time}")
        for name in ("open", "high", "low", "close"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        return self

    @property
    def close_time(self) -> datetime:
        return self.open_time + _seconds_to_delta(self.timeframe.seconds)

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2.0


class Tick(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    time: datetime
    bid: float | None = None
    ask: float | None = None
    last: float | None = None

    @field_validator("time")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _ensure_utc(v)

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2.0

    @property
    def price(self) -> float | None:
        if self.last is not None:
            return self.last
        return self.mid

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid


class InstrumentMetadata(BaseModel):
    """Everything the spec asks for per instrument: tick size, contract size,
    minimum stop distance, typical spread, trading hours - loaded from the
    provider, never hardcoded (spec, Markets section)."""

    canonical_id: str
    symbol: str
    market_class: MarketClass
    venue: str
    quote_currency: str = ""
    base_currency: str = ""
    digits: int | None = None
    tick_size: float | None = None
    contract_size: float | None = None
    min_stop_distance: float | None = None
    typical_spread: float | None = None
    price_grid: float | None = None          # Step indices move in fixed increments
    trading_hours: dict[str, Any] | None = None
    value_per_price_unit: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class SymbolInfo(BaseModel):
    canonical_id: str
    display: str
    market_class: MarketClass
    venue: str
    provider_symbols: dict[str, str] = Field(default_factory=dict)


class ModuleSignal(BaseModel):
    """The record every module returns (spec, Picking one setup)."""

    module_name: str
    module_version: str
    direction: Direction
    entry: float
    stop: float
    target: float
    formation_time: datetime
    known_time: datetime
    status: EventStatus
    params_version: str
    params_sha256: str
    symbol: str
    timeframe: Timeframe
    score: float | None = None                # a rank, never presented as a probability
    probability: float | None = None          # only from a calibrated model (Phase 7)
    reasons: list[str] = Field(default_factory=list)
    invalidation: list[str] = Field(default_factory=list)
    features: dict[str, float] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("known_time", "formation_time")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _ensure_utc(v)

    @model_validator(mode="after")
    def _no_lookahead(self) -> "ModuleSignal":
        if self.known_time < self.formation_time:
            raise ValueError("known_time precedes formation_time")
        return self

    @property
    def reward_risk(self) -> float:
        risk = abs(self.entry - self.stop)
        if risk == 0:
            return float("inf")
        return abs(self.target - self.entry) / risk


def _seconds_to_delta(seconds: int):
    from datetime import timedelta
    return timedelta(seconds=seconds)