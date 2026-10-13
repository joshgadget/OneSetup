"""Phase 6 tests: the event-driven simulator, its outcome labels and its cost model.

Every test here is a hand-built candle fixture with a known answer, and the first one
exists to fail if the simulator ever fills an order on the candle whose close produced
the signal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.backtest.costs import CostModel, funding_R
from engine.backtest.outcomes import EXPIRED, STOP, TARGET, UNFILLED, label_outcome
from engine.backtest.simulator import simulate
from engine.config import load_params
from engine.models import Candle, Direction, Timeframe
from engine.modules.common import make_signal

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
params = load_params()


def candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(symbol="TEST", timeframe=Timeframe.H1, open_time=T0 + timedelta(hours=i),
                  open=o, high=h, low=l, close=c)


def sig(candles, index, direction, entry, stop, target):
    return make_signal(
        module="fixture", version="0.0.0", symbol="TEST", timeframe=Timeframe.H1,
        direction=direction, entry=entry, stop=stop, target=target,
        candle=candles[index], known_index=index, candles=candles,
    )


def flat_warmup(n: int = 6) -> list[Candle]:
    return [candle(i, 100, 100.6, 99.4, 100) for i in range(n)]


def test_a_signal_cannot_fill_on_its_own_candle():
    candles = flat_warmup(5)
    candles.append(candle(5, 100, 100.5, 97.0, 100.0))     # pierces the entry hard
    candles.append(candle(6, 100, 100.5, 98.95, 100.2))    # first legal retest
    candles.append(candle(7, 100.2, 103.4, 100.1, 103.2))  # target 103.0
    signal = sig(candles, 5, Direction.LONG, 99.0, 98.0, 103.0)
    trades = simulate([signal], candles, params)
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fill_index == 6, "the order may only fill after the signal candle"
    assert trade.fill_price == 99.0
    assert trade.outcome == TARGET and trade.exit_index == 7
    assert trade.r_gross == pytest.approx(4.0)


def test_a_limit_that_is_never_retested_never_fills():
    candles = flat_warmup(6) + [candle(i, 100, 100.6, 99.4, 100) for i in range(6, 12)]
    signal = sig(candles, 5, Direction.LONG, 95.0, 94.0, 99.0)
    trades = simulate([signal], candles, params, entry_expiry=3)
    assert trades[0].outcome == UNFILLED
    assert trades[0].fill_index is None
    assert trades[0].r_net == 0.0


def test_stop_first_and_target_first_are_distinguished():
    candles = flat_warmup(5)
    candles.append(candle(5, 100, 100.2, 99.8, 100.0))
    candles.append(candle(6, 100, 100.4, 98.9, 99.2))     # fills at 99.0, holds the stop
    candles.append(candle(7, 99.2, 99.3, 97.5, 98.2))      # stops at 98.0
    stop_signal = sig(candles, 5, Direction.LONG, 99.0, 98.0, 101.0)
    trade = simulate([stop_signal], candles, params)[0]
    assert trade.outcome == STOP and trade.r_gross == pytest.approx(-1.0)
    assert trade.exit_index == 7

    candles2 = flat_warmup(5)
    candles2.append(candle(5, 100, 100.2, 99.8, 100.0))
    candles2.append(candle(6, 100, 99.0, 98.9, 99.2))
    candles2.append(candle(7, 99.2, 101.4, 99.1, 101.2))
    target_signal = sig(candles2, 5, Direction.LONG, 99.0, 98.0, 101.0)
    trade2 = simulate([target_signal], candles2, params)[0]
    assert trade2.outcome == TARGET and trade2.r_gross == pytest.approx(2.0)


def test_a_candle_holding_both_levels_takes_the_stop():
    candles = flat_warmup(5)
    candles.append(candle(5, 100, 100.2, 99.8, 100.0))
    candles.append(candle(6, 100, 101.5, 97.5, 100.5))    # both levels inside one candle
    signal = sig(candles, 5, Direction.LONG, 99.0, 98.0, 101.0)
    trade = simulate([signal], candles, params)[0]
    assert trade.outcome == STOP, "intrabar order is unknown, so the stop wins"
    assert trade.ambiguous is True
    assert trade.r_gross == pytest.approx(-1.0)


def test_short_targets_and_stops_mirror_the_long_rules():
    candles = flat_warmup(5)
    candles.append(candle(5, 100, 100.2, 99.8, 100.0))
    candles.append(candle(6, 99.6, 99.8, 98.9, 99.5))      # fills the short at 99.0
    candles.append(candle(7, 99.5, 99.6, 96.8, 97.2))      # target 97.0
    signal = sig(candles, 5, Direction.SHORT, 99.0, 100.0, 97.0)
    trade = simulate([signal], candles, params)[0]
    assert trade.outcome == TARGET
    assert trade.r_gross == pytest.approx(2.0)


def test_unresolved_trades_exit_at_market_at_expiry():
    candles = flat_warmup(5)
    candles.append(candle(5, 100, 100.2, 99.8, 100.0))
    for i in range(6, 12):
        candles.append(candle(i, 100, 100.4, 98.9, 99.6))   # fills, never reaches either level
    signal = sig(candles, 5, Direction.LONG, 99.0, 98.0, 101.0)
    trade = simulate([signal], candles, params, outcome_expiry=4)[0]
    assert trade.outcome == EXPIRED
    assert trade.exit_index == 9
    assert trade.r_gross == pytest.approx(0.6)
    assert trade.bars_in_trade == 4


def test_costs_and_funding_are_subtracted_in_r():
    candles = flat_warmup(5)
    candles.append(candle(5, 100, 100.2, 99.8, 100.0))
    candles.append(candle(6, 100, 101.4, 98.9, 101.2))
    signal = sig(candles, 5, Direction.LONG, 99.0, 98.0, 101.0)
    costs = CostModel(spread_price=0.20)
    trade = simulate([signal], candles, params, costs=costs)[0]
    assert trade.r_gross == pytest.approx(2.0)
    assert trade.cost_r == pytest.approx(0.2), "0.20 of spread over 1.00 of risk is 0.2 R"
    assert trade.r_net == pytest.approx(1.8)

    funding = funding_R(rate_per_day=0.001, price=100.0, holding_days=1.0,
                        risk_distance=1.0, direction=Direction.LONG)
    assert funding == pytest.approx(0.1), "a long pays a positive funding rate"
    paid = funding_R(rate_per_day=0.001, price=100.0, holding_days=1.0,
                     risk_distance=1.0, direction=Direction.SHORT)
    assert paid == pytest.approx(-0.1), "a short receives it"


def test_label_outcome_expiry_walks_no_further_than_the_expiry_window():
    candles = flat_warmup(4)
    candles.append(candle(4, 100, 100.1, 99.9, 100.0))
    candles.append(candle(5, 100, 100.1, 99.9, 100.0))
    candles.append(candle(6, 100, 103.0, 99.9, 102.5))    # target three candles after the fill
    resolution = label_outcome(candles, direction=Direction.LONG, entry=100.0, stop=99.0,
                               target=102.0, fill_index=4, expiry_candles=2)
    assert resolution.outcome == EXPIRED, "the window ends before the target arrives"
    assert resolution.exit_index == 5
