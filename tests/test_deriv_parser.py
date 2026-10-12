"""Deriv adapter tests. The candle figures below are clearly synthetic test values
(round numbers) used only to check the parse and closed-candle logic - they are not
presented as market evidence anywhere."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.data.deriv import DerivAdapter
from engine.models import Timeframe


async def test_candles_parse_and_unfinished_dropped(monkeypatch):
    adapter = DerivAdapter(app_id="test")
    now = datetime.now(timezone.utc)
    base_epoch = int((now - timedelta(hours=3)).timestamp() // 900 * 900)
    rows = [
        {"epoch": base_epoch + i * 900, "open": 100.0 + i, "high": 101.0 + i,
         "low": 99.0 + i, "close": 100.5 + i}
        for i in range(3)
    ]
    forming = {"epoch": int(now.timestamp() // 900 * 900), "open": 200.0, "high": 201.0,
               "low": 199.0, "close": 200.5}

    async def fake_call(payload: dict) -> dict:
        assert payload["style"] == "candles"
        assert payload["granularity"] == 900
        return {"candles": rows + [forming]}

    monkeypatch.setattr(adapter, "_call", fake_call)
    candles = await adapter.get_candles("R_75", Timeframe.M15, limit=10)
    assert len(candles) == 3
    assert candles[0].open == 100.0
    assert candles[-1].close == 102.5
    assert all(c.is_closed for c in candles)
    assert candles[0].symbol == "R_75" and candles[0].source == "deriv"


async def test_app_id_is_optional_on_public_endpoint():
    adapter = DerivAdapter(app_id="")
    assert adapter.url.endswith("/public")
    adapter_with_id = DerivAdapter(app_id="1089")
    assert "app_id=1089" in adapter_with_id.url