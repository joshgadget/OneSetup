"""Metrics per module, on trades that resolved (spec, Validation and risk).

Win rate over resolved trades, profit factor, expectancy in R, trade count, maximum
drawdown and average time in trade, all after spread, commission and slippage. The
Wilson interval is written exactly as the spec writes it. A small sample can look
excellent by luck, so nothing here is reported without its count.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Sequence


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The spec's Wilson 95% interval for k hits in n trades."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= k <= n:
        raise ValueError("k must be between 0 and n")
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True)
class Metrics:
    trades: int
    resolved: int              # target or stop, the trades the win rate is computed on
    wins: int
    win_rate: float | None
    wilson_low: float | None
    wilson_high: float | None
    profit_factor: float | None
    expectancy_r: float | None       # mean R over all trades that have an outcome
    expectancy_resolved_r: float | None
    max_drawdown_r: float
    avg_bars_in_trade: float | None
    total_cost_r: float
    total_funding_r: float
    expired: int
    unfilled: int
    ambiguous: int
    baseline_p0: float | None        # mean 1/(1+R) of the same setups
    beats_baseline: bool | None      # Wilson lower bound above the baseline

    def to_dict(self) -> dict:
        return asdict(self)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def compute_metrics(trades: Sequence, *, z: float = 1.96) -> Metrics:
    """Trade records as produced by engine.backtest.simulator.simulate."""
    filled = [t for t in trades if t.outcome not in ("unfilled", "no_target")]
    resolved = [t for t in filled if t.outcome in ("target", "stop")]
    wins = [t for t in resolved if t.r_net > 0]
    losses = [t for t in resolved if t.r_net <= 0]

    gross_profit = sum(t.r_net for t in wins)
    gross_loss = sum(t.r_net for t in losses)
    profit_factor = None
    if losses:
        profit_factor = gross_profit / abs(gross_loss)
    elif wins:
        profit_factor = float("inf")

    low = high = None
    if resolved:
        low, high = wilson_interval(len(wins), len(resolved), z=z)

    ordered = sorted(filled, key=lambda t: (t.exit_index if t.exit_index is not None else 0))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for trade in ordered:
        equity += trade.r_net
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    p0_values = []
    for trade in filled:
        risk = abs(trade.entry - trade.stop)
        if risk > 0:
            reward = abs(trade.target - trade.entry) / risk
            p0_values.append(1.0 / (1.0 + reward))
    baseline = _mean(p0_values)

    beats = None
    if low is not None and baseline is not None:
        beats = low > baseline

    return Metrics(
        trades=len(trades),
        resolved=len(resolved),
        wins=len(wins),
        win_rate=(len(wins) / len(resolved)) if resolved else None,
        wilson_low=low,
        wilson_high=high,
        profit_factor=profit_factor,
        expectancy_r=_mean([t.r_net for t in filled]),
        expectancy_resolved_r=_mean([t.r_net for t in resolved]),
        max_drawdown_r=max_dd,
        avg_bars_in_trade=_mean([float(t.bars_in_trade) for t in filled if t.bars_in_trade]),
        total_cost_r=sum(t.cost_r for t in filled),
        total_funding_r=sum(t.funding_r for t in filled),
        expired=sum(1 for t in trades if t.outcome == "expired"),
        unfilled=sum(1 for t in trades if t.outcome == "unfilled"),
        ambiguous=sum(1 for t in trades if t.ambiguous),
        baseline_p0=baseline,
        beats_baseline=beats,
    )
