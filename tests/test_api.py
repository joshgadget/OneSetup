from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from engine.config import get_settings
from engine.data.store import CandleStore
from engine.models import Candle, Timeframe
from services.api import deps
from services.api.main import app


def make_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api.sqlite3"))
    deps.get_store.cache_clear()
    get_settings.cache_clear()
    return TestClient(app)


def test_health_reports_params_hash(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["params_version"] == "params.v1"
    assert len(body["params_sha256"]) == 64


def test_symbols_include_seed_and_candles_roundtrip(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    ids = [s["canonical_id"] for s in client.get("/symbols").json()["symbols"]]
    assert {"GBPUSD", "XAUUSD", "BTC/USD:BTC", "R_75"} <= set(ids)

    r = client.get("/candles", params={"symbol": "GBPUSD", "timeframe": "H1", "store_only": True})
    assert r.status_code == 200 and r.json()["count"] == 0

    store: CandleStore = deps.get_store()
    t0 = datetime.now(timezone.utc) - timedelta(hours=10)
    store.upsert_candles([
        Candle(symbol="GBPUSD", timeframe=Timeframe.H1, open_time=t0 + timedelta(hours=i),
               open=1.25, high=1.26, low=1.24, close=1.255, tick_volume=5, source="test")
        for i in range(3)
    ])
    body = client.get("/candles", params={"symbol": "GBPUSD", "timeframe": "H1", "store_only": True}).json()
    assert body["count"] == 3
    assert body["candles"][0]["time"] < body["candles"][-1]["time"]
    assert body["stale"] is True


def test_unknown_symbol_and_bad_timeframe(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    assert client.get("/candles", params={"symbol": "NOPE", "timeframe": "H1"}).status_code == 404
    assert client.get("/candles", params={"symbol": "GBPUSD", "timeframe": "H7"}).status_code == 400