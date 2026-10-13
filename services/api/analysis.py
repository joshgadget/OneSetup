"""The analysis service: one symbol in, one setup card or "no setup" out.

This is the single place the website and (later) the MT5 client both call, so the
analysis is written once on the server and the clients stay thin.

The order of work is the spec's pipeline: closed candles only, the foundation once,
every module independently, the symbol gate, the calibrated model, then the show rule.
The heavy part is honest about its sample: history is split in time order, the model is
fitted on the first part, checked on the second, and the candidates shown are the ones
from the last part, scored against a history that excludes them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from engine.backtest.candidates import SeriesContext
from engine.backtest.costs import CostModel
from engine.backtest.metrics import compute_metrics
from engine.backtest.splits import time_split
from engine.backtest.study import module_signals_and_trades
from engine.config import load_params
from engine.models import Candle, Direction
from engine.modules import crt as crt_module
from engine.modules import ict as ict_module
from engine.modules import liquidity as liquidity_module
from engine.modules import msnr as msnr_module
from engine.modules import smc as smc_module
from engine.modules import supply_demand as sd_module
from engine.modules import support_resistance as sr_module
from engine.scoring.gate import symbol_gate
from engine.scoring.pipeline import choose_setup, fit_and_calibrate, rank_candidates, test_calibration

ANALYSIS_VERSION = "1.0.0"
MAX_OVERLAY_ITEMS = 300


def _iso(candle: Candle) -> str:
    return candle.open_time.isoformat()


def overlays_from_context(ctx: SeriesContext) -> dict:
    """Everything the detectors found, as chart primitives with a time on every row."""
    candles = ctx.candles
    foundation = ctx.foundation
    params = ctx.params
    levels, _ = sr_module.discover_levels(candles, foundation, params, collect_signals=False)
    zones = sd_module.detect_zones(candles, foundation, params)
    fvgs = smc_module.detect_fvgs(candles, foundation, params)
    blocks = smc_module.detect_order_blocks(candles, foundation, params)
    structure = smc_module.detect_structure_events(candles, foundation, params)
    mss = smc_module.detect_mss(candles, foundation, params)
    msnr_levels = msnr_module.detect_levels(candles, foundation, params,
                                            tick_size=ctx.tick_size)
    crt_setups = crt_module.detect_crt(candles, foundation, params,
                                       reference_candles=ctx.reference_candles)
    judas = ict_module.detect_judas_swing(candles, foundation, params)
    po3 = ict_module.detect_power_of_three(candles, foundation, params)

    def tail(rows: list) -> list:
        return rows[-MAX_OVERLAY_ITEMS:]

    return {
        "symbol": candles[0].symbol,
        "timeframe": candles[0].timeframe.value,
        "as_of": _iso(candles[-1]),
        "counts": {
            "levels": len(levels), "zones": len(zones), "pools": len(ctx.pools),
            "grabs": len(ctx.grabs), "fvgs": len(fvgs), "order_blocks": len(blocks),
            "structure_events": len(structure), "mss": len(mss),
            "msnr_levels": len(msnr_levels), "crt": len(crt_setups),
            "judas": len(judas), "power_of_three": len(po3),
        },
        "levels": [
            {
                "kind": str(getattr(level.kind, "value", level.kind)),
                "lower": level.lower, "upper": level.upper, "centre": level.centre,
                "strength": level.strength, "bounces": level.bounces,
                "penetrations": level.penetrations,
                "formed_index": level.formed_index, "known_index": level.known_index,
                "time": _iso(candles[min(level.known_index, len(candles) - 1)]),
            }
            for level in tail(levels)
        ],
        "zones": [
            {
                "kind": zone.kind, "formation": zone.formation,
                "lower": zone.lower, "upper": zone.upper,
                "proximal": zone.proximal, "distal": zone.distal,
                "score": zone.score, "fresh": zone.fresh,
                "touches": len(zone.touches), "departure_atr": zone.departure_atr,
                "known_index": zone.known_index,
                "time": _iso(candles[min(zone.known_index, len(candles) - 1)]),
            }
            for zone in tail(zones)
        ],
        "pools": [
            {
                "side": pool.side.value, "level": pool.level, "state": pool.state,
                "size": pool.size, "known_index": pool.known_index,
                "swept_index": pool.swept_index,
                "time": _iso(candles[min(pool.known_index, len(candles) - 1)]),
            }
            for pool in tail(ctx.pools)
        ],
        "grabs": [
            {
                "side": grab.side.value, "pool_level": grab.pool_level,
                "variant": grab.variant, "depth_atr": grab.depth_atr,
                "rejection_ratio": grab.rejection_ratio,
                "known_index": grab.known_index, "pierce_index": grab.pierce_index,
                "extreme": grab.extreme,
                "time": _iso(candles[min(grab.known_index, len(candles) - 1)]),
            }
            for grab in tail(ctx.grabs)
        ],
        "fvgs": [
            {
                "direction": fvg.direction, "lower": fvg.lower, "upper": fvg.upper,
                "width_atr": fvg.width_atr, "variant": fvg.variant,
                "index": fvg.index, "mitigated": not fvg.unmitigated,
                "time": _iso(candles[min(fvg.index, len(candles) - 1)]),
            }
            for fvg in tail(fvgs)
        ],
        "order_blocks": [
            {
                "direction": block.direction, "lower": block.lower, "upper": block.upper,
                "variant": block.variant, "candle_index": block.candle_index,
                "mitigated": not block.unmitigated,
                "time": _iso(candles[min(block.candle_index, len(candles) - 1)]),
            }
            for block in tail(blocks)
        ],
        "structure_events": [
            {
                "kind": event.kind, "direction": event.direction, "level": event.level,
                "index": event.index, "time": _iso(candles[min(event.index, len(candles) - 1)]),
            }
            for event in tail(structure)
        ],
        "mss": [
            {
                "direction": item.direction, "level": item.level, "index": item.index,
                "displacement_body_atr": item.displacement_body_atr,
                "time": _iso(candles[min(item.index, len(candles) - 1)]),
            }
            for item in tail(mss)
        ],
        "msnr_levels": [
            {
                "kind": level.kind, "price": level.price, "lower": level.lower,
                "upper": level.upper, "index": level.index, "known_index": level.known_index,
                "bounces": level.bounces, "penetrations": level.penetrations,
                "bounce_probability": level.bounce_probability, "fresh": level.fresh,
                "time": _iso(candles[min(level.known_index, len(candles) - 1)]),
            }
            for level in tail(msnr_levels)
        ],
        "crt": [
            {
                "direction": setup.direction, "variant": setup.variant,
                "reference_high": setup.reference_high, "reference_low": setup.reference_low,
                "reference_mid": setup.reference_mid, "width_atr": setup.width_atr,
                "pierce_index": setup.pierce_index, "known_index": setup.known_index,
                "entry": setup.entry, "stop": setup.stop, "target_1": setup.target_1,
                "reward_risk": setup.reward_risk,
                "time": _iso(candles[min(setup.known_index, len(candles) - 1)]),
            }
            for setup in tail(crt_setups)
        ],
        "judas": [
            {
                "session": row.get("session"), "side": row.get("side"),
                "pierce_index": row.get("pierce_index"),
                "index": row.get("known_index"), "depth_atr": row.get("depth_atr"),
                "time": _iso(candles[min(row.get("known_index", 0), len(candles) - 1)]),
            }
            for row in tail([j.to_dict() if hasattr(j, "to_dict") else dict(j) for j in judas])
        ],
        "power_of_three": [
            {
                "index": row.get("known_index"), "direction": row.get("direction"),
                "accumulation_low": row.get("accumulation_low"),
                "accumulation_high": row.get("accumulation_high"),
                "distribution_body_atr": row.get("distribution_body_atr"),
                "time": _iso(candles[min(row.get("known_index", 0), len(candles) - 1)]),
            }
            for row in tail([p.to_dict() if hasattr(p, "to_dict") else dict(p) for p in po3])
        ],
    }


def analyse_context(
    ctx: SeriesContext,
    *,
    costs: CostModel | None = None,
    funding_rate_per_day: float | None = None,
) -> dict:
    """The whole pipeline on one prepared series. Returns the setup card data."""
    params = ctx.params
    candles = ctx.candles
    split_cfg = params.gate.split
    splits = time_split(
        len(candles),
        train_fraction=float(split_cfg.train_fraction),
        validation_fraction=float(split_cfg.validation_fraction),
        test_fraction=float(split_cfg.test_fraction),
    )
    per_module: dict[str, dict] = {}
    train_trades, validation_trades, test_trades = [], [], []
    for name in ("support_resistance", "liquidity", "liquidity_retrace", "supply_demand",
                 "smc", "ict", "crt", "msnr", "slp"):
        try:
            signals, trades = module_signals_and_trades(
                ctx, name, costs=costs, funding_rate_per_day=funding_rate_per_day)
        except Exception as exc:                       # a module must not sink the page
            per_module[name] = {"signals": 0, "trades": 0, "error": f"{type(exc).__name__}: {exc}"}
            continue
        per_module[name] = {"signals": len(signals), "trades": len(trades)}
        for trade in trades:
            if splits["train"].contains(trade.known_index):
                train_trades.append(trade)
            elif splits["validation"].contains(trade.known_index):
                validation_trades.append(trade)
            elif splits["test"].contains(trade.known_index):
                test_trades.append(trade)

    fit = fit_and_calibrate(train_trades, validation_trades, params)
    fit.test = test_calibration(fit, test_trades, params)

    history = [t for t in train_trades + validation_trades
               if t.outcome in ("target", "stop")]
    recent = [t.signal for t in test_trades if t.outcome in ("target", "stop", "expired")]
    ranked = rank_candidates(recent, history, fit, params=params)

    levels, _ = sr_module.discover_levels(candles, ctx.foundation, params,
                                          collect_signals=False)
    module_rows = {}
    for name in per_module:
        rows = [t for t in (train_trades + validation_trades + test_trades)
                if t.signal.module_name == name]
        in_test = [t for t in rows if splits["test"].contains(t.known_index)]
        module_rows[name] = compute_metrics(in_test)
    gate = symbol_gate(candles=candles, levels=levels, module_metrics=module_rows,
                       params=params)
    decision = choose_setup(ranked, gate=gate, params=params)

    notes = list(costs.notes) if costs is not None else []
    notes.append(f"history split {splits['train'].size}/{splits['validation'].size}/"
                 f"{splits['test'].size} candles, in time order")
    if not fit.calibrated:
        notes.append("the model is not calibrated on this sample: any number shown is a "
                     "score, never a probability")
    top = ranked[0].to_dict() if ranked else None
    return {
        "analysis_version": ANALYSIS_VERSION,
        "symbol": candles[0].symbol,
        "timeframe": candles[0].timeframe.value,
        "as_of": _iso(candles[-1]),
        "candles": len(candles),
        "split": {name: split.to_dict() for name, split in splits.items()},
        "status": decision["status"],
        "reason": decision.get("reason"),
        "statement": decision.get("statement"),
        "setup": decision.get("candidate") or (top if decision["status"] == "no_setup" and
                                               decision.get("candidate") is None else None),
        "gate": gate,
        "modules": [
            {
                "module": name,
                "signals": per_module[name].get("signals", 0),
                "error": per_module[name].get("error"),
                **{k: v for k, v in gate["modules"].get(name, {}).items() if k != "module"},
            }
            for name in per_module
        ],
        "model": fit.to_dict(),
        "candidates_considered": len(recent),
        "notes": notes,
        "disclaimer": ("Analysis only. No order execution, no position sizing, no advice. "
                       "A number is only called a probability when it comes from a model "
                       "trained on past outcomes and calibrated on a later unseen period."),
    }
