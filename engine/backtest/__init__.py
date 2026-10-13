"""Phase 6 backtester: simulation, outcome labels, metrics, splits and the module study.

The spec's protocol, in its own words: split time in order and never shuffle candles;
fit on train, choose on validation, touch test once; model spread, commission, slippage
and the fact that a limit order at a zone edge may not fill; compare every module alone
against a baseline of the same entries and stops without the pattern condition; count
every variant tried. This package does that and nothing else. It never places an order,
and it reports moneyness in R only, because v1 has no position sizing.
"""

from engine.backtest.costs import CostModel, funding_R
from engine.backtest.metrics import Metrics, compute_metrics, wilson_interval
from engine.backtest.outcomes import EXPIRED, STOP, TARGET, UNFILLED, Resolution, label_outcome
from engine.backtest.simulator import Trade, simulate
from engine.backtest.splits import Split, time_split, walk_forward
from engine.backtest.study import ModuleReport, run_study

__all__ = [
    "CostModel", "funding_R", "Metrics", "compute_metrics", "wilson_interval",
    "EXPIRED", "STOP", "TARGET", "UNFILLED", "Resolution", "label_outcome",
    "Trade", "simulate", "Split", "time_split", "walk_forward",
    "ModuleReport", "run_study",
]
