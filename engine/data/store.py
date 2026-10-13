"""SQLite candle store: candles, instruments, symbols.

SQLite keeps Phase 0 simple and auditable. Access goes through this class only, so
a later move to Parquet/DuckDB for bulk backtesting does not touch detectors or the
API. Timestamps are stored as naive UTC (SQLite has no timezone type) and are
returned timezone-aware.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint, create_engine, delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from engine.models import (
    Candle,
    InstrumentMetadata,
    MarketClass,
    SymbolInfo,
    Timeframe,
    utcnow,
)


class Base(DeclarativeBase):
    pass


class SymbolRow(Base):
    __tablename__ = "symbols"
    canonical_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display: Mapped[str] = mapped_column(String(128))
    market_class: Mapped[str] = mapped_column(String(32))
    venue: Mapped[str] = mapped_column(String(32))
    provider_symbols_json: Mapped[str] = mapped_column(String(512), default="{}")
    tags_json: Mapped[str] = mapped_column(String(1024), default="{}")


class InstrumentRow(Base):
    __tablename__ = "instruments"
    canonical_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(128))
    market_class: Mapped[str] = mapped_column(String(32))
    venue: Mapped[str] = mapped_column(String(32))
    quote_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    base_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    digits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tick_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    contract_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_stop_distance: Mapped[float | None] = mapped_column(Float, nullable=True)
    typical_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_grid: Mapped[float | None] = mapped_column(Float, nullable=True)
    trading_hours_json: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    value_per_price_unit: Mapped[float | None] = mapped_column(Float, nullable=True)
    extra_json: Mapped[str | None] = mapped_column(String(8192), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: _naive_utc(utcnow()))


class CandleRow(Base):
    __tablename__ = "candles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    open_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    tick_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    real_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_closed: Mapped[bool] = mapped_column(default=True)
    source: Mapped[str] = mapped_column(String(64), default="")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: _naive_utc(utcnow()))
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "open_time", name="uq_candle"),)


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _aware_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


class CandleStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}", future=True)
        Base.metadata.create_all(self.engine)
        self._migrate()

    def _migrate(self) -> None:
        """Additive migrations, so a store built by an earlier revision keeps working."""
        with self.engine.begin() as conn:
            cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(symbols)")}
            if "tags_json" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE symbols ADD COLUMN tags_json VARCHAR(1024) DEFAULT '{}'")

    # ------------------------------------------------------------------ candles

    def upsert_candles(self, candles: Iterable[Candle]) -> int:
        rows = [
            {
                "symbol": c.symbol,
                "timeframe": c.timeframe.value,
                "open_time": _naive_utc(c.open_time),
                "open": c.open, "high": c.high, "low": c.low, "close": c.close,
                "tick_volume": c.tick_volume, "real_volume": c.real_volume,
                "is_closed": bool(c.is_closed), "source": c.source,
            }
            for c in candles
        ]
        if not rows:
            return 0
        # dedupe within the batch on the unique key, last one wins
        dedup: dict[tuple[str, str, datetime], dict] = {(r["symbol"], r["timeframe"], r["open_time"]): r for r in rows}
        values = list(dedup.values())
        with Session(self.engine) as session:
            stmt = sqlite_insert(CandleRow).values(values)
            update_cols = {c: getattr(stmt.excluded, c) for c in
                           ("open", "high", "low", "close", "tick_volume", "real_volume", "is_closed", "source")}
            session.execute(stmt.on_conflict_do_update(
                index_elements=["symbol", "timeframe", "open_time"], set_=update_cols))
            session.commit()
        return len(values)

    def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        closed_only: bool = True,
    ) -> list[Candle]:
        stmt = select(CandleRow).where(CandleRow.symbol == symbol, CandleRow.timeframe == timeframe.value)
        if closed_only:
            stmt = stmt.where(CandleRow.is_closed.is_(True))
        if start is not None:
            stmt = stmt.where(CandleRow.open_time >= _naive_utc(start))
        if end is not None:
            stmt = stmt.where(CandleRow.open_time <= _naive_utc(end))
        with Session(self.engine) as session:
            if limit is not None:
                stmt = stmt.order_by(CandleRow.open_time.desc()).limit(int(limit))
                rows = list(reversed(list(session.scalars(stmt))))
            else:
                stmt = stmt.order_by(CandleRow.open_time.asc())
                rows = list(session.scalars(stmt))
        return [
            Candle(
                symbol=r.symbol, timeframe=Timeframe.parse(r.timeframe), open_time=_aware_utc(r.open_time),
                open=r.open, high=r.high, low=r.low, close=r.close,
                tick_volume=r.tick_volume, real_volume=r.real_volume,
                is_closed=bool(r.is_closed), source=r.source or "",
            )
            for r in rows
        ]

    def count(self, symbol: str, timeframe: Timeframe) -> int:
        with Session(self.engine) as session:
            stmt = select(func.count()).select_from(CandleRow).where(
                CandleRow.symbol == symbol, CandleRow.timeframe == timeframe.value)
            return int(session.execute(stmt).scalar_one())

    def latest(self, symbol: str, timeframe: Timeframe) -> Candle | None:
        rows = self.get_candles(symbol, timeframe, limit=1, closed_only=False)
        return rows[-1] if rows else None

    def delete_candles(self, symbol: str, timeframe: Timeframe | None = None) -> int:
        with Session(self.engine) as session:
            stmt = delete(CandleRow).where(CandleRow.symbol == symbol)
            if timeframe is not None:
                stmt = stmt.where(CandleRow.timeframe == timeframe.value)
            result = session.execute(stmt)
            session.commit()
            return int(result.rowcount or 0)

    # ------------------------------------------------------------------ instruments

    def upsert_instrument(self, meta: InstrumentMetadata) -> None:
        values = {
            "canonical_id": meta.canonical_id,
            "symbol": meta.symbol,
            "market_class": meta.market_class.value,
            "venue": meta.venue,
            "quote_currency": meta.quote_currency or None,
            "base_currency": meta.base_currency or None,
            "digits": meta.digits,
            "tick_size": meta.tick_size,
            "contract_size": meta.contract_size,
            "min_stop_distance": meta.min_stop_distance,
            "typical_spread": meta.typical_spread,
            "price_grid": meta.price_grid,
            "trading_hours_json": json.dumps(meta.trading_hours) if meta.trading_hours else None,
            "value_per_price_unit": meta.value_per_price_unit,
            "extra_json": json.dumps(meta.extra, default=str) if meta.extra else None,
            "updated_at": _naive_utc(utcnow()),
        }
        with Session(self.engine) as session:
            stmt = sqlite_insert(InstrumentRow).values([values])
            update_cols = {k: getattr(stmt.excluded, k) for k in values if k != "canonical_id"}
            session.execute(stmt.on_conflict_do_update(index_elements=["canonical_id"], set_=update_cols))
            session.commit()

    def get_instrument(self, canonical_id: str) -> InstrumentMetadata | None:
        with Session(self.engine) as session:
            row = session.get(InstrumentRow, canonical_id)
        if row is None:
            return None
        return InstrumentMetadata(
            canonical_id=row.canonical_id, symbol=row.symbol,
            market_class=MarketClass(row.market_class), venue=row.venue,
            quote_currency=row.quote_currency or "", base_currency=row.base_currency or "",
            digits=row.digits, tick_size=row.tick_size, contract_size=row.contract_size,
            min_stop_distance=row.min_stop_distance, typical_spread=row.typical_spread,
            price_grid=row.price_grid,
            trading_hours=json.loads(row.trading_hours_json) if row.trading_hours_json else None,
            value_per_price_unit=row.value_per_price_unit,
            extra=json.loads(row.extra_json) if row.extra_json else {},
        )

    def list_instruments(self) -> list[InstrumentMetadata]:
        with Session(self.engine) as session:
            ids = [r for r in session.scalars(select(InstrumentRow.canonical_id).order_by(InstrumentRow.canonical_id))]
        return [m for m in (self.get_instrument(i) for i in ids) if m is not None]

    # ------------------------------------------------------------------ symbols

    def upsert_symbol(self, info: SymbolInfo) -> None:
        values = {
            "canonical_id": info.canonical_id, "display": info.display,
            "market_class": info.market_class.value, "venue": info.venue,
            "provider_symbols_json": json.dumps(info.provider_symbols),
            "tags_json": json.dumps(info.tags),
        }
        with Session(self.engine) as session:
            stmt = sqlite_insert(SymbolRow).values([values])
            update_cols = {k: getattr(stmt.excluded, k) for k in values if k != "canonical_id"}
            session.execute(stmt.on_conflict_do_update(index_elements=["canonical_id"], set_=update_cols))
            session.commit()

    @staticmethod
    def _symbol_row_to_info(r: SymbolRow) -> SymbolInfo:
        return SymbolInfo(
            canonical_id=r.canonical_id, display=r.display,
            market_class=MarketClass(r.market_class), venue=r.venue,
            provider_symbols=json.loads(r.provider_symbols_json or "{}"),
            tags=json.loads(getattr(r, "tags_json", None) or "{}"),
        )

    def list_symbols(self) -> list[SymbolInfo]:
        with Session(self.engine) as session:
            rows = list(session.scalars(select(SymbolRow).order_by(SymbolRow.canonical_id)))
        return [self._symbol_row_to_info(r) for r in rows]

    def get_symbol(self, canonical_id: str) -> SymbolInfo | None:
        with Session(self.engine) as session:
            row = session.get(SymbolRow, canonical_id)
        return self._symbol_row_to_info(row) if row is not None else None

    def resolve_symbol(self, text: str) -> SymbolInfo | None:
        """Look a symbol up by canonical id, by chip case, or by a provider alias
        (so typing frxGBPUSD or GBP/USD finds GBPUSD)."""
        needle = text.strip()
        if not needle:
            return None
        direct = self.get_symbol(needle) or self.get_symbol(needle.upper())
        if direct is not None:
            return direct
        lowered = needle.lower()
        for info in self.list_symbols():
            if info.canonical_id.lower() == lowered:
                return info
            for alias in info.provider_symbols.values():
                if alias.lower() == lowered:
                    return info
        return None

    def candle_counts(self) -> dict[str, dict[str, object]]:
        """Stored candle count and newest candle per symbol, in one query."""
        with Session(self.engine) as session:
            rows = session.execute(
                select(CandleRow.symbol, func.count(), func.max(CandleRow.open_time))
                .group_by(CandleRow.symbol)
            ).all()
        out: dict[str, dict[str, object]] = {}
        for symbol, count, newest in rows:
            out[str(symbol)] = {
                "candles": int(count),
                "last_candle": _aware_utc(newest).isoformat() if newest is not None else None,
            }
        return out