import pathlib
sp = pathlib.Path(r"C:\Users\Hi\trading\engine\data\store.py")
t = sp.read_text(encoding="utf-8")

old = '''    provider_symbols_json: Mapped[str] = mapped_column(String(512), default="{}")'''
new = '''    provider_symbols_json: Mapped[str] = mapped_column(String(512), default="{}")
    tags_json: Mapped[str] = mapped_column(String(1024), default="{}")'''
assert old in t
t = t.replace(old, new, 1)

old = '''        self.engine = create_engine(f"sqlite:///{self.db_path}", future=True)
        Base.metadata.create_all(self.engine)'''
new = '''        self.engine = create_engine(f"sqlite:///{self.db_path}", future=True)
        Base.metadata.create_all(self.engine)
        self._migrate()

    def _migrate(self) -> None:
        """Additive migrations, so a store built by an earlier revision keeps working."""
        with self.engine.begin() as conn:
            cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(symbols)")}
            if "tags_json" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE symbols ADD COLUMN tags_json VARCHAR(1024) DEFAULT '{}'")'''
assert old in t
t = t.replace(old, new, 1)

old = '''    def upsert_symbol(self, info: SymbolInfo) -> None:
        values = {
            "canonical_id": info.canonical_id, "display": info.display,
            "market_class": info.market_class.value, "venue": info.venue,
            "provider_symbols_json": json.dumps(info.provider_symbols),
        }'''
new = '''    def upsert_symbol(self, info: SymbolInfo) -> None:
        values = {
            "canonical_id": info.canonical_id, "display": info.display,
            "market_class": info.market_class.value, "venue": info.venue,
            "provider_symbols_json": json.dumps(info.provider_symbols),
            "tags_json": json.dumps(info.tags),
        }'''
assert old in t
t = t.replace(old, new, 1)

old = '''    def list_symbols(self) -> list[SymbolInfo]:
        with Session(self.engine) as session:
            rows = list(session.scalars(select(SymbolRow).order_by(SymbolRow.canonical_id)))
        return [
            SymbolInfo(
                canonical_id=r.canonical_id, display=r.display,
                market_class=MarketClass(r.market_class), venue=r.venue,
                provider_symbols=json.loads(r.provider_symbols_json or "{}"),
            )
            for r in rows
        ]'''
new = '''    @staticmethod
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
        return out'''
assert old in t
t = t.replace(old, new, 1)
sp.write_text(t, encoding="utf-8", newline="\n")
print("store.py patched")