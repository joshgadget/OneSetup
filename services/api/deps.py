"""Wiring: store singleton and one cached adapter per provider."""
from __future__ import annotations

from functools import lru_cache

from engine.config import get_settings
from engine.data.base import DataAdapter, DataAdapterError
from engine.data.crypto_ccxt import CCXTAdapter
from engine.data.deriv import DerivAdapter
from engine.data.fxcm import FXCMAdapter
from engine.data.store import CandleStore
from engine.models import MarketClass, SymbolInfo
from engine.data.registry import get_symbol


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
    if info.market_class in (MarketClass.FOREX, MarketClass.GOLD):
        return get_fxcm()
    if info.market_class in (MarketClass.CRYPTO_PERP, MarketClass.CRYPTO_FUTURES):
        return get_ccxt()
    if info.market_class is MarketClass.SYNTHETIC:
        return get_deriv()
    raise DataAdapterError(f"no adapter for market class {info.market_class}")


def adapter_for_symbol(canonical_id: str) -> DataAdapter:
    info = get_symbol(canonical_id)
    if info is None:
        raise DataAdapterError(f"unknown symbol {canonical_id!r}; see GET /symbols")
    return adapter_for(info)