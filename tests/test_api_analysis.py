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
    yield
    api_main._ANALYSIS_CACHE.clear()


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
