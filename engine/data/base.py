"""The one data adapter interface. Every provider implements exactly this.

Adapters return only closed candles unless the caller explicitly asks otherwise,
and they are responsible for normalising provider quirks (price order, epoch units,
duplicate rows) into engine.models.Candle.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import AsyncIterator

from engine.models import Candle, InstrumentMetadata, SymbolInfo, Tick, Timeframe


class DataAdapterError(RuntimeError):
    """Raised for provider errors that the caller cannot fix (auth, limits, schema)."""


class DataAdapter(ABC):
    name: str = "abstract"

    @abstractmethod
    async def list_symbols(self) -> list[SymbolInfo]:
        """Symbols available from this provider, with canonical ids."""

    @abstractmethod
    async def get_instrument_metadata(self, canonical_id: str) -> InstrumentMetadata:
        """Tick size, contract size, minimum stop distance, typical spread, hours."""

    @abstractmethod
    async def get_candles(
        self,
        canonical_id: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        closed_only: bool = True,
    ) -> list[Candle]:
        """Historical candles, oldest first. Unfinished candles are dropped when
        closed_only is true (the default; detectors must always use the default)."""

    @abstractmethod
    def subscribe(self, canonical_id: str) -> AsyncIterator[Tick]:
        """Live ticks. May raise DataAdapterError if the provider lacks streaming."""

    async def aclose(self) -> None:  # pragma: no cover - optional cleanup
        return None

    async def __aenter__(self) -> "DataAdapter":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()