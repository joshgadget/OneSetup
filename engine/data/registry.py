"""Canonical symbol registry: seed symbols, provider aliases, and routing hints.

Canonical ids: compact pairs for FX and metals (GBPUSD, XAUUSD), ccxt unified
symbols for crypto (BTC/USD:BTC), Deriv's own symbol for synthetic indices (R_75).

These are only seeds, so a fresh install can fetch something immediately. The real
catalogue is whatever the providers list, discovered by `engine.data.discovery` and
stored in the `symbols` table.

FX and gold carry aliases for every provider that quotes them, and the venue is
taken from settings (`FOREX_VENUE`, `GOLD_VENUE`), so changing the source of FX data
is an environment variable rather than a code edit.
"""
from __future__ import annotations

from functools import lru_cache

from engine.config import get_settings
from engine.models import MarketClass, SymbolInfo


def _fx_seed(canonical: str, display: str, market_class: MarketClass, deriv: str, fxcm: str) -> SymbolInfo:
    venue = (get_settings().forex_venue if market_class is MarketClass.FOREX else get_settings().gold_venue).upper()
    return SymbolInfo(
        canonical_id=canonical,
        display=display,
        market_class=market_class,
        venue=venue,
        provider_symbols={"deriv": deriv, "fxcm": fxcm},
    )


@lru_cache(maxsize=4)
def seed_symbols() -> tuple[SymbolInfo, ...]:
    return (
        _fx_seed("GBPUSD", "GBP/USD", MarketClass.FOREX, "frxGBPUSD", "GBP/USD"),
        _fx_seed("EURUSD", "EUR/USD", MarketClass.FOREX, "frxEURUSD", "EUR/USD"),
        _fx_seed("USDJPY", "USD/JPY", MarketClass.FOREX, "frxUSDJPY", "USD/JPY"),
        _fx_seed("AUDUSD", "AUD/USD", MarketClass.FOREX, "frxAUDUSD", "AUD/USD"),
        _fx_seed("USDCAD", "USD/CAD", MarketClass.FOREX, "frxUSDCAD", "USD/CAD"),
        _fx_seed("USDCHF", "USD/CHF", MarketClass.FOREX, "frxUSDCHF", "USD/CHF"),
        _fx_seed("XAUUSD", "XAU/USD", MarketClass.GOLD, "frxXAUUSD", "XAU/USD"),
        SymbolInfo(canonical_id="BTC/USD:BTC", display="BTC/USD perpetual", market_class=MarketClass.CRYPTO_PERP,
                   venue="DERIBIT", provider_symbols={"ccxt": "BTC/USD:BTC"}),
        SymbolInfo(canonical_id="ETH/USD:ETH", display="ETH/USD perpetual", market_class=MarketClass.CRYPTO_PERP,
                   venue="DERIBIT", provider_symbols={"ccxt": "ETH/USD:ETH"}),
        SymbolInfo(canonical_id="R_75", display="Volatility 75 Index", market_class=MarketClass.SYNTHETIC,
                   venue="DERIV", provider_symbols={"deriv": "R_75"}),
        SymbolInfo(canonical_id="stpRNG", display="Step Index", market_class=MarketClass.SYNTHETIC,
                   venue="DERIV", provider_symbols={"deriv": "stpRNG"}),
    )


def _index() -> dict[str, SymbolInfo]:
    return {s.canonical_id.upper(): s for s in seed_symbols()}


def get_symbol(canonical_id: str) -> SymbolInfo | None:
    return _index().get(canonical_id.strip().upper())


def resolve(provider: str, provider_symbol: str) -> SymbolInfo | None:
    needle = provider_symbol.strip().upper()
    for s in seed_symbols():
        if s.provider_symbols.get(provider, "").upper() == needle:
            return s
    return None


def symbols_for(market_class: MarketClass) -> list[SymbolInfo]:
    return [s for s in seed_symbols() if s.market_class == market_class]


def all_symbols() -> list[SymbolInfo]:
    return list(seed_symbols())