'''Phase 8 tests: the overlay feed, the analysis card and the gate endpoint.

The promises under test belong to the browser client: every drawn item carries the
candle time it became known at, unfinished candles never leave the API, a number is
only called a probability when the model was calibrated on a later unseen period, and
"no setup" is a first-class answer with a reason.
'''
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from engine.config import get_settings
from engine.data.store import CandleStore
from engine.models import Candle, Direction, Timeframe
from engine.modules.common import make_signal
from engine.scoring.pipeline import RankedCandidate
from services.api import deps
from services.api.main import app

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)

OVERLAY_LISTS = (
    "levels", "zones", "pools", "fvgs", "order_blocks",
    "structure_events", "mss", "msnr_levels", "crt",
)

CARD_FIELDS = {
    "module", "module_version", "direction", "entry", "stop", "target",
    "formation_time", "known_time", "reward_risk", "baseline_p0",
    "probability", "score", "expected_value", "similar", "reasons",
    "invalidation", "notes",
}


@pytest.fixture(autouse=True)
def _empty_analysis_cache():
    from services.api import main as api_main
    api_main._ANALYSIS_CACHE.clear()
    api_main._LAST_PROVIDER_CALL.clear()
    yield
    api_main._ANALYSIS_CACHE.clear()
    api_main._LAST_PROVIDER_CALL.clear()


def make_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api.sqlite3"))
    deps.get_store.cache_clear()
    get_settings.cache_clear()
    return TestClient(app)


def wave_candles(n: int = 420, seed: int = 5, symbol: str = "GBPUSD") -> list[Candle]:
    '''A deterministic random walk big enough for the gate and the module spread.'''
    rng = np.random.default_rng(seed)
    price = 1.2500
    out: list[Candle] = []
    for i in range(n):
        step = float(rng.normal(0.0, 0.0012))
        open_ = price
        close = open_ + step
        high = max(open_, close) + abs(step) * 0.5
        low = min(open_, close) - abs(step) * 0.5
        out.append(Candle(symbol=symbol, timeframe=Timeframe.H1,
                          open_time=T0 + timedelta(hours=i),
                          open=open_, high=high, low=low, close=close,
                          tick_volume=10, source="test"))
        price = close
    return out


def seed_store(candles: list[Candle]) -> CandleStore:
    store = deps.get_store()
    store.upsert_candles(candles)
    return store


def test_overlays_are_drawable_and_never_use_an_unfinished_candle(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    candles = wave_candles(420)
    unfinished = Candle(symbol="GBPUSD", timeframe=Timeframe.H1,
                        open_time=T0 + timedelta(hours=420),
                        open=1.25, high=1.26, low=1.24, close=1.255, is_closed=False)
    seed_store([*candles, unfinished])

    body = client.get("/overlays/GBPUSD", params={"timeframe": "H1", "bars": 500}).json()

    assert body["as_of"] == candles[-1].open_time.isoformat(), "an unfinished candle leaked"
    for key in OVERLAY_LISTS:
        rows = body[key]
        assert isinstance(rows, list), key
        for row in rows:
            assert "time" in row, f"{key} row without a time: {row}"
            assert row["time"] <= body["as_of"], f"{key} row from after as_of"
    assert {"levels", "zones", "pools", "grabs", "fvgs", "order_blocks"} <= set(body["counts"])


def test_analysis_returns_one_card_or_an_explicit_no_setup(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    seed_store(wave_candles(420))

    body = client.get("/analysis/GBPUSD", params={"timeframe": "H1", "bars": 500}).json()

    assert body["status"] in ("setup", "no_setup")
    assert body["gate"]["passed"] in (True, False)
    assert body["analysis_version"]
    assert "high probability" not in json.dumps(body).lower()
    if body["status"] == "no_setup":
        assert isinstance(body["reason"], str) and body["reason"].strip()
    else:
        assert CARD_FIELDS <= set(body["setup"])
        assert body["setup"]["probability"] is not None
    if not body["model"]["calibrated"] and body["setup"] is not None:
        assert body["setup"]["probability"] is None


def test_analysis_holds_the_look_ahead_line(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    candles = wave_candles(420)
    seed_store(candles)

    body = client.get("/analysis/GBPUSD", params={"timeframe": "H1", "bars": 500}).json()

    assert body["as_of"] == candles[-1].open_time.isoformat()
    assert body["modules"], "the analysis must report every module, even the failing ones"
    for row in body["modules"]:
        assert row["module"]
    if body["setup"] is not None:
        assert body["setup"]["known_time"] <= body["as_of"]
        assert body["setup"]["formation_time"] <= body["setup"]["known_time"]


def test_setup_card_passes_the_full_record_through(tmp_path, monkeypatch):
    '''The site reads these fields, so the endpoint must not trim the module record.'''
    client = make_client(tmp_path, monkeypatch)
    candles = wave_candles(420)
    seed_store(candles)
    signal = make_signal(
        module="fixture", version="9.9.9", symbol="GBPUSD", timeframe=Timeframe.H1,
        direction=Direction.LONG, entry=1.25, stop=1.24, target=1.27,
        candle=candles[300], known_index=300, candles=candles,
        reasons=["hand-built card"], invalidation=["close below 1.24"],
    )
    card = RankedCandidate(
        signal=signal, reward_risk=2.0, baseline_p0=1.0 / 3.0, probability=0.44,
        score=0.44, expected_value=0.11, cost_r=0.02,
        similar={"cases": 40, "hits": 18, "hit_rate": 0.45,
                 "interval": (0.30, 0.60), "radius": 0.5},
        notes=("fixture",),
    ).to_dict()
    import services.api.analysis as analysis_module
    monkeypatch.setattr(analysis_module, "choose_setup",
                        lambda *a, **k: {"status": "setup", "candidate": card,
                                         "statement": "fixture statement"})

    body = client.get("/analysis/GBPUSD", params={"timeframe": "H1", "bars": 500}).json()

    assert body["status"] == "setup"
    assert CARD_FIELDS <= set(body["setup"])
    assert body["setup"]["reasons"] == ["hand-built card"]
    assert body["setup"]["invalidation"] == ["close below 1.24"]
    assert body["statement"] == "fixture statement"


def test_analysis_is_cached_and_the_gate_endpoint_agrees(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    seed_store(wave_candles(420))

    first = client.get("/analysis/GBPUSD", params={"timeframe": "H1", "bars": 500}).json()
    second = client.get("/analysis/GBPUSD", params={"timeframe": "H1", "bars": 500}).json()
    gate = client.get("/gate/GBPUSD", params={"timeframe": "H1", "bars": 500}).json()

    assert first["cached"] is False and second["cached"] is True
    assert second["as_of"] == first["as_of"] and second["status"] == first["status"]
    assert gate["gate"]["passed"] == first["gate"]["passed"]
    assert "setup" not in gate

def test_analysis_says_plainly_when_there_is_too_little_history(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    seed_store(wave_candles(40))

    response = client.get("/analysis/GBPUSD", params={"timeframe": "H1", "bars": 500})

    assert response.status_code == 409
    assert "at least 200" in response.json()["detail"]


def test_symbol_ids_with_a_slash_are_routable(tmp_path, monkeypatch):
    '''Crypto ids carry a slash (BTC/USD:BTC); the routes must accept them as one id.'''
    client = make_client(tmp_path, monkeypatch)
    seed_store(wave_candles(240, seed=9, symbol="BTC/USD:BTC"))

    response = client.get("/overlays/BTC/USD:BTC", params={"timeframe": "H1", "bars": 300})

    assert response.status_code == 200
    assert response.json()["symbol"] == "BTC/USD:BTC"

class FakeAdapter:
    """A provider stand-in for the refresh, throttle and resample tests."""

    def __init__(self, by_timeframe):
        self.by_timeframe = by_timeframe
        self.calls: list[tuple[str, str]] = []

    async def get_candles(self, canonical_id, timeframe, start=None, end=None, limit=None,
                          closed_only=True):
        from engine.data.base import DataAdapterError
        self.calls.append((canonical_id, timeframe.value))
        if timeframe not in self.by_timeframe:
            raise DataAdapterError(f"fake provider does not serve {timeframe.value}")
        return list(self.by_timeframe[timeframe])[-int(limit or 500):]


def test_candles_refresh_polls_the_provider_once_and_then_throttles(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    candles = wave_candles(300)
    seed_store(candles)
    newer = [
        Candle(symbol="GBPUSD", timeframe=Timeframe.H1,
               open_time=candles[-1].open_time + timedelta(hours=offset),
               open=1.25, high=1.26, low=1.24, close=1.255, tick_volume=3, source="test")
        for offset in range(1, 6)
    ]
    import services.api.main as api_main
    fake = FakeAdapter({Timeframe.H1: [*candles, *newer]})
    monkeypatch.setattr(api_main, "adapter_for_symbol", lambda canonical_id: fake)

    first = client.get("/candles", params={"symbol": "GBPUSD", "timeframe": "H1",
                                           "limit": 400, "refresh": "true"}).json()
    second = client.get("/candles", params={"symbol": "GBPUSD", "timeframe": "H1",
                                            "limit": 400, "refresh": "true"}).json()

    assert first["refreshed"] is True and first["source"] != "store"
    assert first["count"] == 305 and len(fake.calls) == 1
    assert second["refreshed"] is False and second["refresh_skipped"]
    assert len(fake.calls) == 1, "the provider must not be polled twice inside the window"


def test_weekly_candles_are_resampled_when_the_venue_has_none(tmp_path, monkeypatch):
    """A 24/7 market trades every day, so its weekly bucket is complete at seven."""
    client = make_client(tmp_path, monkeypatch)
    day = datetime(2026, 1, 1, tzinfo=timezone.utc)
    daily = [
        Candle(symbol="BTC/USD:BTC", timeframe=Timeframe.D1, open_time=day + timedelta(days=i),
               open=62000.0, high=63000.0, low=61000.0, close=62500.0, tick_volume=7,
               source="test")
        for i in range(90)
    ]
    seed_store(daily)
    import services.api.main as api_main
    fake = FakeAdapter({Timeframe.D1: daily})
    monkeypatch.setattr(api_main, "adapter_for_symbol", lambda canonical_id: fake)

    body = client.get("/candles", params={"symbol": "BTC/USD:BTC", "timeframe": "W1",
                                          "limit": 50}).json()

    assert body["symbol"] == "BTC/USD:BTC"
    assert body["timeframe"] == "W1"
    assert body["source"].endswith("+resample")
    assert body["count"] >= 8
    for row in body["candles"]:
        stamp = int(datetime.fromisoformat(row["time"]).timestamp())
        assert stamp % Timeframe.W1.seconds == 0
        assert row["closed"] is True


def test_overlays_carry_the_engine_atr_for_the_indicator_pane(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    candles = wave_candles(300)
    seed_store(candles)

    body = client.get("/overlays/GBPUSD", params={"timeframe": "H1", "bars": 400}).json()

    assert body["atr_period"] == 14
    from engine.config import load_params
    from engine.foundation import compute_foundation
    foundation = compute_foundation(candles, k=int(load_params().foundation.pivot.k_by_timeframe["H1"]))
    expected = [(candles[i].open_time.isoformat(), foundation.atr_at(i))
                for i in range(len(candles)) if foundation.atr_at(i) is not None]
    assert len(body["atr"]) == len(expected) == len(candles) - 13
    assert body["atr"][-1]["time"] == expected[-1][0]
    assert abs(body["atr"][-1]["value"] - expected[-1][1]) < 1e-12

def test_a_forex_week_is_five_daily_candles_not_seven(tmp_path, monkeypatch):
    '''Session markets trade five days a week, so a weekly bucket is complete at five.'''
    client = make_client(tmp_path, monkeypatch)
    day = datetime(2026, 1, 5, tzinfo=timezone.utc)  # a Monday
    daily = [
        Candle(symbol="GBPUSD", timeframe=Timeframe.D1,
               open_time=day + timedelta(days=week * 7 + weekday),
               open=1.25, high=1.26, low=1.24, close=1.255, tick_volume=7, source="test")
        for week in range(12)
        for weekday in range(5)
    ]
    seed_store(daily)
    import services.api.main as api_main
    fake = FakeAdapter({Timeframe.D1: daily})
    monkeypatch.setattr(api_main, "adapter_for_symbol", lambda canonical_id: fake)

    body = client.get("/candles", params={"symbol": "GBPUSD", "timeframe": "W1",
                                          "limit": 20}).json()

    assert body["count"] >= 10
    assert body["source"].endswith("+resample")
    for row in body["candles"]:
        assert datetime.fromisoformat(row["time"]).weekday() == 3  # epoch-aligned weeks start Thursday
