"""Offline adapter over CSV fixtures: used by tests, never presented as a result.

CSV format: time_iso,open,high,low,close,volume  (time is UTC, candle open time).
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from engine.data.base import DataAdapter, DataAdapterError
from engine.models import Candle, InstrumentMetadata, MarketClass, SymbolInfo, Tick, Timeframe


class FixtureAdapter(DataAdapter):
    name = "fixtures"

    def __init__(self, root: Path, market_class: MarketClass = MarketClass.FOREX, venue: str = "FIXTURE") -> None:
        self.root = Path(root)
        self.market_class = market_class
        self.venue = venue

    def _path(self, canonical_id: str, timeframe: Timeframe) -> Path:
        return self.root / f"{canonical_id}_{timeframe.value}.csv"

    async def list_symbols(self) -> list[SymbolInfo]:
        out: list[SymbolInfo] = []
        for path in sorted(self.root.glob("*.csv")):
            stem = path.stem
            if "_" not in stem:
                continue
            symbol, tf = stem.rsplit("_", 1)
            try:
                Timeframe.parse(tf)
            except ValueError:
                continue
            if not any(s.canonical_id == symbol for s in out):
                out.append(
                    SymbolInfo(
                        canonical_id=symbol, display=symbol, market_class=self.market_class,
                        venue=self.venue, provider_symbols={"fixture": symbol},
                    )
                )
        return out

    async def get_instrument_metadata(self, canonical_id: str) -> InstrumentMetadata:
        return InstrumentMetadata(
            canonical_id=canonical_id, symbol=canonical_id, market_class=self.market_class,
            venue=self.venue, extra={"note": "fixture metadata is minimal by design"},
        )

    async def get_candles(
        self,
        canonical_id: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        closed_only: bool = True,
    ) -> list[Candle]:
        path = self._path(canonical_id, timeframe)
        if not path.exists():
            raise DataAdapterError(f"fixture not found: {path}")
        rows: list[Candle] = []
        with path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                ts = datetime.fromisoformat(row["time"]).astimezone(timezone.utc)
                if start and ts < start:
                    continue
                if end and ts > end:
                    continue
                rows.append(
                    Candle(
                        symbol=canonical_id, timeframe=timeframe, open_time=ts,
                        open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]),
                        tick_volume=float(row.get("volume") or 0) or None,
                        is_closed=True, source="fixture",
                    )
                )
        rows.sort(key=lambda c: c.open_time)
        if limit:
            rows = rows[-int(limit):]
        return rows

    async def subscribe(self, canonical_id: str) -> AsyncIterator[Tick]:  # type: ignore[override]
        raise DataAdapterError("fixture adapter has no live stream")
        yield  # pragma: no cover