"""Event-driven simulation of module signals.

Rules, all of them no-look-ahead:
* a signal is known at the close of candle k, so its resting order is live from candle
  k + 1 and never on k itself;
* a limit order fills only when a later candle's range touches the entry price, so a
  zone edge that is never retested never fills (execution.limit_fill = touch);
* the order expires after execution.entry_expiry_candles candles if it never fills;
* the outcome is target, stop, or the market exit at expiry;
* spread, commission, slippage and funding are subtracted in R. Funding is only
  modelled when a funding rate is supplied, and the note says so when it is not.

Trades carry the whole signal so Phase 7 can fit on the feature vector without a join.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from engine.config import load_params
from engine.models import Candle, Direction, ModuleSignal
from engine.backtest.costs import CostModel, funding_R
from engine.backtest.outcomes import UNFILLED, label_outcome

SECONDS_PER_DAY = 86400.0


@dataclass
class Trade:
    signal: ModuleSignal
    direction: Direction
    entry: float
    stop: float
    target: float
    risk_distance: float
    reward_risk: float
    known_index: int
    fill_index: int | None = None
    fill_price: float | None = None
    exit_index: int | None = None
    exit_price: float | None = None
    outcome: str | None = None
    r_gross: float = 0.0
    cost_r: float = 0.0
    funding_r: float = 0.0
    r_net: float = 0.0
    bars_in_trade: int | None = None
    ambiguous: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def module(self) -> str:
        return self.signal.module_name

    @property
    def fill_time(self):
        return self.signal.meta.get("_fill_time")

    def to_dict(self) -> dict:
        return {
            "module": self.signal.module_name,
            "module_version": self.signal.module_version,
            "direction": self.direction.value,
            "symbol": self.signal.symbol,
            "timeframe": self.signal.timeframe.value,
            "known_time": self.signal.known_time.isoformat(),
            "known_index": self.known_index,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "reward_risk": self.reward_risk,
            "fill_index": self.fill_index,
            "exit_index": self.exit_index,
            "exit_price": self.exit_price,
            "outcome": self.outcome,
            "r_gross": self.r_gross,
            "cost_r": self.cost_r,
            "funding_r": self.funding_r,
            "r_net": self.r_net,
            "bars_in_trade": self.bars_in_trade,
            "ambiguous": self.ambiguous,
        }


def index_signals(signals: Sequence[ModuleSignal], candles: Sequence[Candle]) -> dict:
    """Map each signal to the candle index whose close made it knowable."""
    by_time = {}
    for i, candle in enumerate(candles):
        by_time.setdefault(candle.open_time, i)
    out = {}
    for signal in signals:
        index = by_time.get(signal.known_time)
        if index is None:
            raise ValueError(
                f"signal known_time {signal.known_time} is not the open time of any candle: "
                "signals must be stamped at a candle open (engine.modules.common.make_signal)"
            )
        out[id(signal)] = index
    return out


def simulate(
    signals: Sequence[ModuleSignal],
    candles: Sequence[Candle],
    params=None,
    *,
    costs: CostModel | None = None,
    funding_rate_per_day: float | None = None,
    entry_expiry: int | None = None,
    outcome_expiry: int | None = None,
) -> list[Trade]:
    """Simulate every signal in time order and return one trade record per signal."""
    params = params or load_params()
    candles = list(candles)
    if not candles:
        return []
    positions = index_signals(signals, candles)
    costs = costs or CostModel()
    entry_expiry = int(entry_expiry if entry_expiry is not None
                       else params.execution.entry_expiry_candles)
    outcome_expiry = int(outcome_expiry if outcome_expiry is not None
                         else params.execution.outcome_expiry_candles)
    fill_mode = str(params.execution.limit_fill)
    exit_at_market = str(params.execution.unresolved_expiry_exit) == "market"

    trades: list[Trade] = []
    for signal in signals:
        known_index = positions[id(signal)]
        risk = abs(signal.entry - signal.stop)
        if risk <= 0:
            continue
        direction = signal.direction
        start = known_index + 1
        deadline = min(start + entry_expiry - 1, len(candles) - 1)
        fill_index = None
        fill_price = None
        for j in range(start, deadline + 1):
            candle = candles[j]
            touched = candle.low <= signal.entry if direction is Direction.LONG else candle.high >= signal.entry
            if not touched:
                continue
            fill_index = j
            fill_price = signal.entry
            if fill_mode == "touch_or_better":
                if direction is Direction.LONG and candle.open < signal.entry:
                    fill_price = candle.open
                elif direction is Direction.SHORT and candle.open > signal.entry:
                    fill_price = candle.open
            break

        reward_risk = abs(signal.target - signal.entry) / risk
        trade = Trade(
            signal=signal, direction=direction, entry=signal.entry, stop=signal.stop,
            target=signal.target, risk_distance=risk, reward_risk=reward_risk,
            known_index=known_index,
        )
        if fill_index is None:
            trade.outcome = UNFILLED
            trade.notes = ("the limit was never retested before the order expired",)
            trades.append(trade)
            continue

        entry = float(fill_price)
        risk = abs(entry - signal.stop)
        resolution = label_outcome(
            candles, direction=direction, entry=entry, stop=signal.stop,
            target=signal.target, fill_index=fill_index, expiry_candles=outcome_expiry,
            exit_unresolved_at_market=exit_at_market,
        )
        cost_r = costs.cost_R(risk)
        holding_days = (resolution.bars_in_trade * candles[fill_index].timeframe.seconds
                        / SECONDS_PER_DAY)
        funding_r = 0.0
        if funding_rate_per_day:
            funding_r = funding_R(funding_rate_per_day, entry, holding_days, risk, direction)
        trade.fill_index = fill_index
        trade.fill_price = entry
        trade.exit_index = resolution.exit_index
        trade.exit_price = resolution.exit_price
        trade.outcome = resolution.outcome
        trade.r_gross = resolution.r_gross
        trade.cost_r = cost_r
        trade.funding_r = funding_r
        trade.r_net = resolution.r_gross - cost_r - funding_r
        trade.bars_in_trade = resolution.bars_in_trade
        trade.ambiguous = resolution.ambiguous
        trade.notes = tuple(costs.notes)
        trades.append(trade)
    return trades
