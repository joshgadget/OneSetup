"""FXCM adapter tests over recorded shapes - no network access.

The candle rows below are the exact sample rows published in the official FXCM
Socket REST API User Guide, section 7 (sample response), so the Open/Close/High/Low
ordering quirk is tested against FXCM's own example rather than invented data.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx
import pytest
import respx

from engine.data.fxcm import FXCMAdapter, _iter_price_events, compact
from engine.models import Timeframe

SOCKET_BODY = (
    '97:0{"sid":"HHGqC3Gao2ENa5tNAAEu","upgrades":["websocket"],'
    '"pingInterval":25000,"pingTimeout":60000}'
)

# Official sample rows: [ts, BidO, BidC, BidH, BidL, AskO, AskC, AskH, AskL, Qty]
DOC_SAMPLE_ROWS = [
    [1498125600, 1.11641, 1.11643, 1.11664, 1.11592, 1.11664, 1.11667, 1.11688, 1.11616, 10423],
    [1498129200, 1.11643, 1.11641, 1.11654, 1.11558, 1.11667, 1.11664, 1.11677, 1.11580, 11229],
]

OFFERS_PAYLOAD = {
    "response": {"executed": True},
    "Offers": [{"OfferId": 3, "Symbol": "GBP/USD", "fractionDigits": 5, "pip": 0.0001,
                "Bid": 1.25, "Ask": 1.2502}],
}


def make_adapter(price_side: str = "mid") -> FXCMAdapter:
    return FXCMAdapter(access_token="a" * 40, host="api-demo.fxcm.com", price_side=price_side)


def mock_routes(mock: respx.MockRouter, rows: list) -> None:
    mock.get(url__startswith="https://api-demo.fxcm.com/socket.io/").mock(
        return_value=httpx.Response(200, text=SOCKET_BODY))
    mock.get(url__startswith="https://api-demo.fxcm.com/trading/get_model/").mock(
        return_value=httpx.Response(200, json=OFFERS_PAYLOAD))
    mock.get(url__startswith="https://api-demo.fxcm.com/candles/3/h1/").mock(
        return_value=httpx.Response(200, json={"response": {"executed": True, "error": ""},
                                               "instrument_id": "3", "period_id": "h1", "candles": rows}))


async def test_parses_documented_sample_rows_as_mid():
    adapter = make_adapter("mid")
    with respx.mock(assert_all_called=False) as mock:
        mock_routes(mock, DOC_SAMPLE_ROWS)
        candles = await adapter.get_candles("GBPUSD", Timeframe.H1, limit=10, closed_only=False)
    assert len(candles) == 2
    first = candles[0]
    assert first.open_time == datetime.fromtimestamp(1498125600, tz=timezone.utc)
    assert first.open == pytest.approx((1.11641 + 1.11664) / 2)
    assert first.high == pytest.approx((1.11664 + 1.11688) / 2)
    assert first.low == pytest.approx((1.11592 + 1.11616) / 2)
    assert first.close == pytest.approx((1.11643 + 1.11667) / 2)
    assert first.tick_volume == 10423
    assert first.source == "fxcm:mid"
    assert first.is_closed is True


async def test_bid_side_keeps_fxcm_open_close_high_low_order():
    adapter = make_adapter("bid")
    with respx.mock(assert_all_called=False) as mock:
        mock_routes(mock, DOC_SAMPLE_ROWS)
        candles = await adapter.get_candles("GBPUSD", Timeframe.H1, limit=10, closed_only=False)
    first = candles[0]
    assert first.open == pytest.approx(1.11641)   # not the high
    assert first.close == pytest.approx(1.11643)
    assert first.high == pytest.approx(1.11664)
    assert first.low == pytest.approx(1.11592)


async def test_duplicate_timestamps_collapse_and_unfinished_drop():
    now = datetime.now(timezone.utc)
    forming_ts = int(time.time() // 3600 * 3600)
    rows = DOC_SAMPLE_ROWS + [
        [DOC_SAMPLE_ROWS[0][0], 1.2, 1.2, 1.3, 1.1, 1.2, 1.2, 1.3, 1.1, 999],
        [forming_ts, 1.30, 1.30, 1.31, 1.29, 1.30, 1.30, 1.31, 1.29, 500],
    ]
    adapter = make_adapter("mid")
    with respx.mock(assert_all_called=False) as mock:
        mock_routes(mock, rows)
        closed = await adapter.get_candles("GBPUSD", Timeframe.H1, limit=10, closed_only=True)
        everything = await adapter.get_candles("GBPUSD", Timeframe.H1, limit=10, closed_only=False)
    # duplicate ts collapsed to the later row in the payload
    assert len(closed) == 2 and closed[0].open == pytest.approx((1.2 + 1.2) / 2)
    assert len(everything) == 3
    assert everything[-1].is_closed is False
    assert everything[-1].open_time <= now


async def test_get_model_tolerates_lowercase_key():
    payload = {"response": {"executed": True}, "offers": OFFERS_PAYLOAD["Offers"]}
    adapter = make_adapter()
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith="https://api-demo.fxcm.com/socket.io/").mock(
            return_value=httpx.Response(200, text=SOCKET_BODY))
        mock.get(url__startswith="https://api-demo.fxcm.com/trading/get_model/").mock(
            return_value=httpx.Response(200, json=payload))
        meta = await adapter.get_instrument_metadata("GBPUSD")
    assert meta.tick_size == pytest.approx(0.0001)
    assert meta.digits == 5
    assert meta.typical_spread == pytest.approx(0.0002)


def test_price_event_frame_parsing():
    frame = ('97:42["EUR/USD","{\\"Updated\\":1501275479,\\"Rates\\":[1.17481,1.17513,1.17653,1.167],'
             '\\"Symbol\\":\\"EUR/USD\\"}"]')
    events = list(_iter_price_events(frame))
    assert len(events) == 1
    symbol, tick = events[0]
    assert compact(symbol) == "EURUSD"
    assert tick.bid == pytest.approx(1.17481)
    assert tick.ask == pytest.approx(1.17513)
    assert tick.time == datetime.fromtimestamp(1501275479, tz=timezone.utc)