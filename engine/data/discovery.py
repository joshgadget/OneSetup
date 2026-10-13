"""Symbol discovery: ask each provider what it lists, and keep the answer.

`engine.data.registry` holds a handful of seed symbols so a fresh install can fetch
something immediately. This module is how the catalogue grows to everything a
provider actually offers: Deriv's full active-symbol list, and every crypto
perpetual and dated future the exchange lists.

The only hand-written knowledge here is the mapping from a provider's own taxonomy
(Deriv's `market` / `submarket` strings, ccxt's market flags) onto our market
classes, which decide session and gap handling. Everything else is read live.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from engine.data.base import DataAdapter, DataAdapterError
from engine.models import MarketClass, SymbolInfo

log = logging.getLogger(__name__)

# Deriv returns forex, metals and crypto under prefixed symbols (frxGBPUSD,
# cryBTCUSD). The prefix is stripped so one canonical id - GBPUSD - can carry
# aliases for every provider that quotes it.
_DERIV_PREFIXES = ("frx", "cry")

_DERIV_CLASS_BY_MARKET: dict[str, MarketClass] = {
    "synthetic_index": MarketClass.SYNTHETIC,
    "forex": MarketClass.FOREX,
    "indices": MarketClass.INDEX,
    "cryptocurrency": MarketClass.CRYPTO_SPOT,
}


def deriv_canonical_id(raw_symbol: str) -> str:
    """frxGBPUSD -> GBPUSD, cryBTCUSD -> BTCUSD, 1HZ100V -> 1HZ100V."""
    lowered = raw_symbol.lower()
    for prefix in _DERIV_PREFIXES:
        if lowered.startswith(prefix) and len(raw_symbol) > len(prefix):
            return raw_symbol[len(prefix):]
    return raw_symbol


def deriv_market_class(row: dict[str, Any]) -> MarketClass | None:
    market = str(row.get("market") or "")
    if market == "commodities":
        # Deriv groups gold, silver, platinum and palladium under one market.
        canonical = deriv_canonical_id(str(row.get("underlying_symbol") or ""))
        return MarketClass.GOLD if canonical.upper().startswith("XAU") else MarketClass.COMMODITY
    return _DERIV_CLASS_BY_MARKET.get(market)


def symbol_from_deriv_row(row: dict[str, Any]) -> SymbolInfo | None:
    raw = str(row.get("underlying_symbol") or row.get("symbol") or "").strip()
    if not raw:
        return None
    market_class = deriv_market_class(row)
    if market_class is None:
        log.debug("Deriv symbol %s has unmapped market %r, skipped", raw, row.get("market"))
        return None
    display = str(row.get("underlying_symbol_name") or row.get("display_name") or raw)
    return SymbolInfo(
        canonical_id=deriv_canonical_id(raw),
        display=display,
        market_class=market_class,
        venue="DERIV",
        provider_symbols={"deriv": raw},
        tags={
            "deriv_market": str(row.get("market") or ""),
            "deriv_submarket": str(row.get("submarket") or ""),
            "deriv_type": str(row.get("underlying_symbol_type") or ""),
        },
    )


def symbols_from_deriv_rows(rows: Iterable[dict[str, Any]]) -> list[SymbolInfo]:
    out: list[SymbolInfo] = []
    seen: set[str] = set()
    for row in rows:
        info = symbol_from_deriv_row(row)
        if info is None or info.canonical_id in seen:
            continue
        seen.add(info.canonical_id)
        out.append(info)
    out.sort(key=lambda s: (s.market_class.value, s.canonical_id))
    return out


def merge_symbols(existing: SymbolInfo | None, new: SymbolInfo) -> SymbolInfo:
    """Combine provider aliases when two providers quote the same instrument.

    Canonical id stays the same, so GBPUSD keeps its fxcm alias when Deriv starts
    serving it, and the venue becomes the provider that actually answered.
    """
    if existing is None:
        return new
    merged_providers = {**existing.provider_symbols, **new.provider_symbols}
    merged_tags = {**existing.tags, **new.tags}
    return new.model_copy(
        update={"provider_symbols": merged_providers, "tags": merged_tags}
    )


async def discover_from_adapter(adapter: DataAdapter) -> list[SymbolInfo]:
    """Whatever the adapter can list, with provider errors surfaced not hidden."""
    try:
        symbols = await adapter.list_symbols()
    except DataAdapterError:
        raise
    except Exception as exc:  # provider SDKs raise their own error types
        raise DataAdapterError(f"{adapter.name} symbol discovery failed: {exc}") from exc
    return list(symbols)


async def discover_and_store(store: Any, adapters: dict[str, DataAdapter]) -> dict[str, Any]:
    """Discover from each adapter and upsert into the store.

    Returns a per-provider report so a caller (or the UI) can see exactly what
    happened, including providers that failed and why - never a silent zero.
    """
    report: dict[str, Any] = {"providers": {}, "total_stored": 0}
    for name, adapter in adapters.items():
        entry: dict[str, Any] = {"ok": False, "found": 0, "stored": 0, "error": None}
        try:
            found = await discover_from_adapter(adapter)
        except DataAdapterError as exc:
            entry["error"] = str(exc)
            report["providers"][name] = entry
            continue
        by_class: dict[str, int] = {}
        for info in found:
            existing = store.get_symbol(info.canonical_id)
            store.upsert_symbol(merge_symbols(existing, info))
            by_class[info.market_class.value] = by_class.get(info.market_class.value, 0) + 1
        entry.update(ok=True, found=len(found), stored=len(found), by_market_class=by_class)
        report["providers"][name] = entry
        report["total_stored"] += len(found)
    return report