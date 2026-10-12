from datetime import datetime, timedelta, timezone

from engine.data.store import CandleStore
from engine.models import Candle, InstrumentMetadata, MarketClass, SymbolInfo, Timeframe


def make_candle(open_time: datetime, close: float = 1.05, is_closed: bool = True) -> Candle:
    return Candle(symbol="GBPUSD", timeframe=Timeframe.H1, open_time=open_time,
                  open=1.0, high=max(1.1, close), low=0.9, close=close,
                  tick_volume=10, is_closed=is_closed, source="test")


def test_upsert_roundtrip_and_ordering(tmp_path):
    store = CandleStore(tmp_path / "t.sqlite3")
    base = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    candles = [make_candle(base + timedelta(hours=i), close=1.0 + i / 100) for i in range(3)]
    assert store.upsert_candles(candles) == 3
    assert store.count("GBPUSD", Timeframe.H1) == 3

    got = store.get_candles("GBPUSD", Timeframe.H1)
    assert [c.open_time for c in got] == [c.open_time for c in candles]
    assert got[-1].close == 1.02

    limited = store.get_candles("GBPUSD", Timeframe.H1, limit=2)
    assert [c.open_time for c in limited] == [c.open_time for c in candles[1:]]


def test_upsert_same_key_updates_and_dedupes(tmp_path):
    store = CandleStore(tmp_path / "t.sqlite3")
    t0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
    store.upsert_candles([make_candle(t0, close=1.01)])
    store.upsert_candles([make_candle(t0, close=1.02)])
    rows = store.get_candles("GBPUSD", Timeframe.H1)
    assert len(rows) == 1 and rows[0].close == 1.02

    dup = [make_candle(t0 + timedelta(hours=1), close=1.03), make_candle(t0 + timedelta(hours=1), close=1.04)]
    assert store.upsert_candles(dup) == 1
    assert store.get_candles("GBPUSD", Timeframe.H1)[-1].close == 1.04


def test_closed_only_filter(tmp_path):
    store = CandleStore(tmp_path / "t.sqlite3")
    t0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
    store.upsert_candles([make_candle(t0), make_candle(t0 + timedelta(hours=1), is_closed=False)])
    assert store.count("GBPUSD", Timeframe.H1) == 2
    assert len(store.get_candles("GBPUSD", Timeframe.H1, closed_only=True)) == 1
    assert len(store.get_candles("GBPUSD", Timeframe.H1, closed_only=False)) == 2
    assert store.latest("GBPUSD", Timeframe.H1).is_closed is False


def test_symbol_and_instrument_roundtrip(tmp_path):
    store = CandleStore(tmp_path / "t.sqlite3")
    store.upsert_symbol(SymbolInfo(canonical_id="XAUUSD", display="XAU/USD",
                                   market_class=MarketClass.GOLD, venue="FXCM",
                                   provider_symbols={"fxcm": "XAU/USD"}))
    assert store.list_symbols()[0].provider_symbols["fxcm"] == "XAU/USD"

    meta = InstrumentMetadata(canonical_id="XAUUSD", symbol="XAU/USD", market_class=MarketClass.GOLD,
                              venue="FXCM", digits=2, tick_size=0.01,
                              extra={"raw_offer": {"OfferId": 5}})
    store.upsert_instrument(meta)
    got = store.get_instrument("XAUUSD")
    assert got is not None and got.tick_size == 0.01 and got.extra["raw_offer"]["OfferId"] == 5