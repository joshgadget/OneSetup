import pathlib
dp = pathlib.Path(r"C:\Users\Hi\trading\engine\data\deriv.py")
t = dp.read_text(encoding="utf-8")

old = '''import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

import websockets

from engine.data.base import DataAdapter, DataAdapterError'''
new = '''import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

import websockets

from engine.data.base import DataAdapter, DataAdapterError
from engine.data.discovery import deriv_market_class, symbols_from_deriv_rows'''
assert old in t
t = t.replace(old, new, 1)

old = '''    def __init__(self, app_id: str = "", ws_url: str = DEFAULT_WS_URL, *, timeout: float = 20.0) -> None:
        self.app_id = app_id
        self.url = f"{ws_url}?app_id={app_id}" if app_id else ws_url
        self.timeout = timeout
        self._req_id = 0'''
new = '''    def __init__(
        self,
        app_id: str = "",
        ws_url: str = DEFAULT_WS_URL,
        *,
        timeout: float = 20.0,
        symbol_ttl: float = 600.0,
    ) -> None:
        self.app_id = app_id
        self.url = f"{ws_url}?app_id={app_id}" if app_id else ws_url
        self.timeout = timeout
        self._req_id = 0
        # canonical id -> Deriv's own symbol (frxGBPUSD for GBPUSD). The symbol
        # list is a websocket round trip, so it is cached rather than fetched per call.
        self.symbol_ttl = symbol_ttl
        self._symbol_index: dict[str, str] | None = None
        self._symbol_index_at: float = 0.0'''
assert old in t
t = t.replace(old, new, 1)

old = '''    async def list_symbols(self) -> list[SymbolInfo]:
        rows = await self._active_symbols()
        out: list[SymbolInfo] = []
        for row in rows:
            symbol = self._row_symbol(row)
            if not symbol:
                continue
            out.append(
                SymbolInfo(
                    canonical_id=symbol,
                    display=self._row_display(row) or symbol,
                    market_class=MarketClass.SYNTHETIC,
                    venue="DERIV",
                    provider_symbols={"deriv": symbol},
                )
            )
        out.sort(key=lambda s: s.canonical_id)
        return out'''
new = '''    async def list_symbols(self) -> list[SymbolInfo]:
        """Every instrument Deriv currently lists, mapped onto our market classes.

        Canonical ids strip Deriv's own prefixes (frxGBPUSD -> GBPUSD, cryBTCUSD ->
        BTCUSD) so one instrument has one id across providers; synthetic indices keep
        Deriv's symbol (R_75, 1HZ100V, stpRNG).
        """
        rows = await self._active_symbols()
        return symbols_from_deriv_rows(rows)

    async def provider_symbol(self, canonical_id: str) -> str:
        """Deriv's own symbol for a canonical id, refreshing the cache on a miss."""
        index = await self._symbol_index_map()
        if canonical_id in index:
            return index[canonical_id]
        self._symbol_index = None  # maybe a newly listed instrument: refetch once
        index = await self._symbol_index_map()
        return index.get(canonical_id, canonical_id)

    async def _symbol_index_map(self) -> dict[str, str]:
        now = time.monotonic()
        if self._symbol_index is None or (now - self._symbol_index_at) > self.symbol_ttl:
            rows = await self._active_symbols()
            self._symbol_index = {
                info.canonical_id: info.provider_symbols.get("deriv", info.canonical_id)
                for info in symbols_from_deriv_rows(rows)
            }
            self._symbol_index_at = now
        return self._symbol_index'''
assert old in t
t = t.replace(old, new, 1)

old = '''    async def get_instrument_metadata(self, canonical_id: str) -> InstrumentMetadata:
        rows = await self._active_symbols()
        row = next((r for r in rows if self._row_symbol(r) == canonical_id), None)
        if row is None:
            raise DataAdapterError(f"Deriv does not list symbol {canonical_id!r}")
        pip = self._row_pip(row)
        return InstrumentMetadata(
            canonical_id=canonical_id,
            symbol=self._row_display(row) or canonical_id,
            market_class=MarketClass.SYNTHETIC,
            venue="DERIV",'''
new = '''    async def get_instrument_metadata(self, canonical_id: str) -> InstrumentMetadata:
        provider = await self.provider_symbol(canonical_id)
        rows = await self._active_symbols()
        row = next((r for r in rows if self._row_symbol(r) == provider), None)
        if row is None:
            raise DataAdapterError(f"Deriv does not list symbol {canonical_id!r}")
        pip = self._row_pip(row)
        market_class = deriv_market_class(row) or MarketClass.SYNTHETIC
        return InstrumentMetadata(
            canonical_id=canonical_id,
            symbol=self._row_display(row) or canonical_id,
            market_class=market_class,
            venue="DERIV",'''
assert old in t
t = t.replace(old, new, 1)

old = '''        granularity = _GRANULARITY.get(timeframe)
        if granularity is None:
            raise DataAdapterError(f"Deriv does not support timeframe {timeframe.value}")
        count = int(limit) if limit else 1000
        payload: dict[str, Any] = {
            "ticks_history": canonical_id,'''
new = '''        granularity = _GRANULARITY.get(timeframe)
        if granularity is None:
            raise DataAdapterError(f"Deriv does not support timeframe {timeframe.value}")
        count = int(limit) if limit else 1000
        provider = await self.provider_symbol(canonical_id)
        payload: dict[str, Any] = {
            "ticks_history": provider,'''
assert old in t
t = t.replace(old, new, 1)

old = '''            async with websockets.connect(self.url, open_timeout=self.timeout) as ws:
                await ws.send(json.dumps({"ticks": canonical_id, "subscribe": 1, "req_id": self._next_id()}))'''
new = '''            provider = await self.provider_symbol(canonical_id)
            async with websockets.connect(self.url, open_timeout=self.timeout) as ws:
                await ws.send(json.dumps({"ticks": provider, "subscribe": 1, "req_id": self._next_id()}))'''
assert old in t
t = t.replace(old, new, 1)
dp.write_text(t, encoding="utf-8", newline="\n")
print("deriv.py patched")