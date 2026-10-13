"""Wiring: store singleton, one cached adapter per provider, and symbol routing.

Routing is by venue, not by market class, so a new instrument family (index CFDs,
metals, spot crypto) does not need a code change - only the provider that serves it.
The store is consulted before the seed registry, so anything discovery has stored is
fetchable.
"""
from __future__ import annotations

from functools import lru_cache

from engine.config import get_settings
from engine.data.base import DataAdapter, DataAdapterError
from engine.data.crypto_ccxt import CCXTAdapter
from engine.data.deriv import DerivAdapter
from engine.data.fxcm import FXCMAdapter
from engine.data.registry import get_symbol as seed_symbol
from engine.data.store import CandleStore
from engine.models import SymbolInfo


@lru_cache(maxsize=1)
def get_store() -> CandleStore:
    return CandleStore(get_settings().db_path)


@lru_cache(maxsize=1)
def get_fxcm() -> FXCMAdapter:
    s = get_settings()
    return FXCMAdapter(access_token=s.fxcm_access_token, host=s.fxcm_host, price_side=s.fxcm_price_side)


@lru_cache(maxsize=1)
def get_ccxt() -> CCXTAdapter:
    return CCXTAdapter(exchange=get_settings().crypto_exchange)


@lru_cache(maxsize=1)
def get_deriv() -> DerivAdapter:
    return DerivAdapter(app_id=get_settings().deriv_app_id)


def adapter_for(info: SymbolInfo) -> DataAdapter:
    venue = info.venue.upper()
    if venue == "FXCM":
        return get_fxcm()
    if venue == "DERIV":
        return get_deriv()
    return get_ccxt()


def find_symbol(canonical_id: str) -> SymbolInfo | None:
    """Store first (discovery result), then the seed registry, then alias lookup."""
    store = get_store()
    return store.get_symbol(canonical_id) or store.resolve_symbol(canonical_id) or seed_symbol(canonical_id)


def adapter_for_symbol(canonical_id: str) -> DataAdapter:
    info = find_symbol(canonical_id)
    if info is None:
        raise DataAdapterError(f"unknown symbol {canonical_id!r}; see GET /symbols")
    return adapter_for(info)