import pathlib

# ---------- models.py ----------
mp = pathlib.Path(r"C:\Users\Hi\trading\engine\models.py")
t = mp.read_text(encoding="utf-8")

old_enum = '''class MarketClass(str, Enum):
    FOREX = "forex"
    GOLD = "gold"
    CRYPTO_PERP = "crypto_perp"
    CRYPTO_FUTURES = "crypto_futures"
    SYNTHETIC = "synthetic"
'''
new_enum = '''class MarketClass(str, Enum):
    """Instrument families the platform can reason about.

    The spec names forex, gold, crypto futures/perpetuals and Deriv synthetic
    indices. The extra classes exist because the reachable providers also list
    index CFDs, other metals and spot crypto, and those need correct session and
    gap handling rather than being forced into the wrong family.
    """

    FOREX = "forex"
    GOLD = "gold"
    COMMODITY = "commodity"        # other metals: silver, platinum, palladium
    INDEX = "index"                # cash index CFDs (have daily session breaks)
    CRYPTO_PERP = "crypto_perp"
    CRYPTO_FUTURES = "crypto_futures"
    CRYPTO_SPOT = "crypto_spot"    # spot / CFD crypto: no funding, no expiry
    SYNTHETIC = "synthetic"

    @property
    def trades_continuously(self) -> bool:
        """True for instruments with no session breaks at all."""
        return self in (
            MarketClass.CRYPTO_PERP,
            MarketClass.CRYPTO_FUTURES,
            MarketClass.CRYPTO_SPOT,
            MarketClass.SYNTHETIC,
        )

    @property
    def has_sessions(self) -> bool:
        return not self.trades_continuously
'''
assert old_enum in t
t = t.replace(old_enum, new_enum)

old_sym = '''class SymbolInfo(BaseModel):
    canonical_id: str
    display: str
    market_class: MarketClass
    venue: str
    provider_symbols: dict[str, str] = Field(default_factory=dict)
'''
new_sym = '''class SymbolInfo(BaseModel):
    canonical_id: str
    display: str
    market_class: MarketClass
    venue: str
    provider_symbols: dict[str, str] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)

    @property
    def providers(self) -> list[str]:
        return sorted(self.provider_symbols)
'''
assert old_sym in t
t = t.replace(old_sym, new_sym)
mp.write_text(t, encoding="utf-8", newline="\n")
print("models.py patched")

# ---------- integrity.py ----------
ip = pathlib.Path(r"C:\Users\Hi\trading\engine\data\integrity.py")
t = ip.read_text(encoding="utf-8")

old = '''def find_gaps(
    candles: Sequence[Candle],
    timeframe: Timeframe,
    market_class: MarketClass,
    *,
    allowed_break_minutes: int = 0,
) -> list[Gap]:
    """Gaps between consecutive candles, classified.

    Forex and gold have weekend breaks; gold also has a short daily break. Crypto
    and synthetic indices trade 24/7, so any gap there is unexpected and blocks
    signals (spec: no signal from a gap in the candle series).
    """
    out: list[Gap] = []
    if len(candles) < 2:
        return out
    step = timedelta(seconds=timeframe.seconds)
    tolerance = timedelta(minutes=allowed_break_minutes)
    for prev, nxt in zip(candles, candles[1:]):
        delta = nxt.open_time - prev.open_time
        if delta <= step + tolerance:
            continue
        missing = max(0, int((delta - tolerance) / step) - 1)
        if missing == 0:
            continue
        if market_class in (MarketClass.FOREX, MarketClass.GOLD) and _is_weekend_span(prev.close_time, nxt.open_time):
            kind = "weekend"
        elif market_class in (MarketClass.CRYPTO_PERP, MarketClass.CRYPTO_FUTURES, MarketClass.SYNTHETIC):
            kind = "unexpected"
        else:
            kind = "expected" if allowed_break_minutes and delta <= step + tolerance + step else "unexpected"
        out.append(Gap(prev.symbol, timeframe, prev.close_time, nxt.open_time, missing, kind))
    return out'''

new = '''# Default session-break allowance per instrument family, in minutes. These cover the
# routine daily maintenance breaks (gold and metals pause ~1 hour, cash index CFDs
# pause between their sessions). Instrument-specific hours are still loaded from the
# provider where it publishes them; this is only the fallback.
_DEFAULT_BREAK_MINUTES: dict[MarketClass, int] = {
    MarketClass.GOLD: 60,
    MarketClass.COMMODITY: 60,
    MarketClass.INDEX: 60,
}


def default_break_minutes(market_class: MarketClass) -> int:
    return _DEFAULT_BREAK_MINUTES.get(market_class, 0)


def find_gaps(
    candles: Sequence[Candle],
    timeframe: Timeframe,
    market_class: MarketClass,
    *,
    allowed_break_minutes: int | None = None,
) -> list[Gap]:
    """Gaps between consecutive candles, classified.

    Session instruments (forex, gold, metals, index CFDs) close for weekends and
    short daily breaks, so those are "weekend" / covered by the break allowance.
    Continuous instruments (crypto, synthetic indices) trade 24/7, so any gap there
    is unexpected and blocks signals (spec: no signal from a gap in the series).
    """
    out: list[Gap] = []
    if len(candles) < 2:
        return out
    if allowed_break_minutes is None:
        allowed_break_minutes = default_break_minutes(market_class)
    step = timedelta(seconds=timeframe.seconds)
    tolerance = timedelta(minutes=allowed_break_minutes)
    for prev, nxt in zip(candles, candles[1:]):
        delta = nxt.open_time - prev.open_time
        if delta <= step + tolerance:
            continue
        missing = max(0, int((delta - tolerance) / step) - 1)
        if missing == 0:
            continue
        if market_class.has_sessions and _is_weekend_span(prev.close_time, nxt.open_time):
            kind = "weekend"
        elif market_class.trades_continuously:
            kind = "unexpected"
        else:
            kind = "unexpected"
        out.append(Gap(prev.symbol, timeframe, prev.close_time, nxt.open_time, missing, kind))
    return out'''
assert old in t
t = t.replace(old, new)
ip.write_text(t, encoding="utf-8", newline="\n")
print("integrity.py patched")