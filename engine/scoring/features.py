"""The feature vector x every candidate carries into the model.

The spec names the features it wants: level strength, zone score, pool size, sweep
depth, normalised distance to the target, reward to risk R, a session flag, and whether
the higher timeframe agrees. It also says not to count votes but to let the model see
the module flags as features, so each module contributes a flag and learns its own
weight.

Every feature here is computable at the signal's known_time from the signal record
itself; nothing in this file looks at a later candle. A missing feature is filled with
0.0 after standardisation, and the count of missing values travels with the model, so
the gap is visible rather than hidden.
"""
from __future__ import annotations

from datetime import timezone
from typing import Iterable, Sequence

import numpy as np

from engine.config import load_params

MODULE_FLAGS = (
    "support_resistance", "liquidity", "liquidity_retrace", "supply_demand",
    "smc", "ict", "crt", "msnr", "slp",
)

NUMERIC_FEATURES = (
    "reward_risk",
    "risk_atr",
    "pool_size",
    "grab_depth_atr",
    "grab_rejection",
    "candles_grab_to_mss",
    "candles_mss_to_entry",
    "zone_score",
    "poi_score",
    "poi_width_atr",
    "poi_distance_atr",
    "level_strength",
    "distance_to_target_atr",
    "bias",
    "htf_agrees",
    "session_flag",
    "hour_sin",
    "hour_cos",
)

FLAGS = MODULE_FLAGS + ("direction_long",)

FEATURE_NAMES = tuple(NUMERIC_FEATURES) + tuple(FLAGS)

# "Similar" for the show rule is measured on this subset only (DECISIONS.md D-015):
# the spec's own list of comparable quantities, without the module flags and time-of-day
# features, which are not comparable between a level detector and a zone detector.
SIMILARITY_FEATURES = (
    "reward_risk",
    "risk_atr",
    "pool_size",
    "grab_depth_atr",
    "zone_score",
    "poi_distance_atr",
    "htf_agrees",
    "session_flag",
    "level_strength",
    "candles_grab_to_mss",
)


def _num(signal, key: str) -> float | None:
    value = signal.features.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _meta_num(signal, *path: str) -> float | None:
    node = signal.meta
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    try:
        return float(node)
    except (TypeError, ValueError):
        return None


def _session_flag(signal, params) -> float:
    """1.0 when the signal's candle falls inside a configured ICT session window."""
    sessions = params.ict.sessions
    local = signal.known_time.astimezone(timezone.utc)
    for name in sessions:
        spec = sessions[name]
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(str(spec["tz"]))
        except Exception:
            continue
        clock = local.astimezone(tz).time()
        start_h, start_m = (int(x) for x in str(spec["start"]).split(":"))
        end_h, end_m = (int(x) for x in str(spec["end"]).split(":"))
        start = start_h * 60 + start_m
        end = end_h * 60 + end_m
        minute = clock.hour * 60 + clock.minute
        if start <= minute < end:
            return 1.0
    return 0.0


def features_from_signal(signal, params=None) -> dict[str, float]:
    """The named features of one candidate, all known at its known_time."""
    params = params or load_params()
    out: dict[str, float] = {}

    def put(name: str, value: float | None) -> None:
        out[name] = float(value) if value is not None else float("nan")

    reward_risk = abs(signal.target - signal.entry) / abs(signal.entry - signal.stop) \
        if signal.entry != signal.stop else 0.0
    put("reward_risk", reward_risk)
    atr = signal.features.get("atr")
    risk = abs(signal.entry - signal.stop)
    put("risk_atr", (risk / float(atr)) if atr else None)
    put("pool_size", _num(signal, "pool_size"))
    put("grab_depth_atr", _num(signal, "grab_depth_atr"))
    put("grab_rejection", _num(signal, "grab_rejection"))
    put("candles_grab_to_mss", _num(signal, "candles_grab_to_mss"))
    put("candles_mss_to_entry", _num(signal, "candles_mss_to_entry"))
    put("zone_score", _num(signal, "zone_score"))
    put("poi_score", _num(signal, "poi_score"))
    put("poi_width_atr", _num(signal, "poi_width_atr"))
    put("poi_distance_atr", _num(signal, "poi_distance_atr"))
    put("level_strength", _num(signal, "strength") or _meta_num(signal, "level", "strength"))
    target_atr = None
    if atr:
        target_atr = abs(signal.target - signal.entry) / float(atr)
    put("distance_to_target_atr", target_atr if target_atr is not None
        else abs(signal.target - signal.entry))
    bias = _num(signal, "bias")
    put("bias", bias)
    if bias is None:
        put("htf_agrees", None)
    else:
        long_ = signal.direction.value == "long"
        put("htf_agrees", 1.0 if (bias > 0) == long_ and bias != 0 else 0.0)
    put("session_flag", _session_flag(signal, params))
    hour = signal.known_time.astimezone(timezone.utc).hour + \
        signal.known_time.astimezone(timezone.utc).minute / 60.0
    put("hour_sin", float(np.sin(2.0 * np.pi * hour / 24.0)))
    put("hour_cos", float(np.cos(2.0 * np.pi * hour / 24.0)))
    for flag in MODULE_FLAGS:
        out[flag] = 1.0 if signal.module_name == flag else 0.0
    out["direction_long"] = 1.0 if signal.direction.value == "long" else 0.0
    return out


def feature_matrix(signals: Sequence, names: Sequence[str] = FEATURE_NAMES,
                   params=None) -> tuple[np.ndarray, dict[str, int]]:
    """X[i, j] for each candidate. NaN counts are returned, not hidden."""
    params = params or load_params()
    names = tuple(names)
    rows = []
    missing = {name: 0 for name in names}
    for signal in signals:
        values = features_from_signal(signal, params)
        row = []
        for name in names:
            value = values.get(name, float("nan"))
            if value is None or (isinstance(value, float) and np.isnan(value)):
                missing[name] += 1
                row.append(np.nan)
            else:
                row.append(float(value))
        rows.append(row)
    matrix = np.array(rows, dtype=float) if rows else np.zeros((0, len(names)))
    return matrix, missing


def target_vector(trades: Iterable) -> np.ndarray:
    """1 when the target came first, 0 when the stop did; expired trades are excluded."""
    out = []
    for trade in trades:
        if trade.outcome == "target":
            out.append(1.0)
        elif trade.outcome == "stop":
            out.append(0.0)
    return np.array(out, dtype=float)
