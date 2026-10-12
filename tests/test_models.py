from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from engine.models import Candle, Direction, EventStatus, ModuleSignal, Tick, Timeframe


def test_timeframe_parse_and_properties():
    tf = Timeframe.parse("m15")
    assert tf is Timeframe.M15
    assert tf.seconds == 900
    assert tf.pandas_freq == "15min"
    with pytest.raises(ValueError):
        Timeframe.parse("H7")


def test_candle_requires_utc_and_sane_prices():
    with pytest.raises(ValidationError):
        Candle(symbol="GBPUSD", timeframe=Timeframe.H1, open_time=datetime(2026, 1, 1),
               open=1.0, high=1.1, low=0.9, close=1.05)
    with pytest.raises(ValidationError):
        Candle(symbol="GBPUSD", timeframe=Timeframe.H1,
               open_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
               open=1.0, high=0.9, low=1.1, close=1.05)


def test_candle_close_time():
    c = Candle(symbol="GBPUSD", timeframe=Timeframe.H4,
               open_time=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
               open=1.0, high=1.1, low=0.9, close=1.05)
    assert c.close_time == datetime(2026, 1, 1, 4, 0, tzinfo=timezone.utc)


def test_tick_price_prefers_last_then_mid():
    assert Tick(symbol="R_75", time=datetime.now(timezone.utc), last=100.5).price == 100.5
    assert Tick(symbol="GBPUSD", time=datetime.now(timezone.utc), bid=1.2, ask=1.2004).price == pytest.approx(1.2002)


def test_signal_no_lookahead_and_reward_risk():
    now = datetime.now(timezone.utc)
    signal = ModuleSignal(
        module_name="sr", module_version="1.0.0", direction=Direction.LONG,
        entry=1.10, stop=1.09, target=1.13,
        formation_time=now - timedelta(hours=2), known_time=now,
        status=EventStatus.CONFIRMED, params_version="params.v1", params_sha256="0" * 64,
        symbol="GBPUSD", timeframe=Timeframe.H1,
    )
    assert signal.reward_risk == pytest.approx(3.0)
    with pytest.raises(ValidationError):
        ModuleSignal(
            module_name="sr", module_version="1.0.0", direction=Direction.LONG,
            entry=1.10, stop=1.09, target=1.13,
            formation_time=now, known_time=now - timedelta(hours=1),
            status=EventStatus.CONFIRMED, params_version="params.v1", params_sha256="0" * 64,
            symbol="GBPUSD", timeframe=Timeframe.H1,
        )