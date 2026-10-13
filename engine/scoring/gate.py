"""The symbol gate: what has to be true before any setup is shown.

The spec runs three checks per symbol, before any module output reaches anyone:

1. the variance ratio over q candles, which equals 1 for a random walk;
2. the support and resistance bounce probability, where a 95% interval that includes
   0.5 means levels carry no information on this symbol;
3. each module's hit rate against the baseline 1/(1+R), with the lower end of the Wilson
   interval above the baseline plus costs, on data the model never saw.

The variance ratio is Lo and MacKinlay (1988), with the heteroskedasticity-robust
statistic because returns are not homoskedastic. The bounce check reuses the counts the
support and resistance module already keeps, so the gate cannot quietly define its own.

On a symmetric synthetic index every one of these is expected to fail, and the honest
output is "no setup" (spec, Markets section).
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from engine.config import load_params
from engine.scoring.stats import wilson_interval


def log_returns(close: Sequence[float]) -> np.ndarray:
    values = np.asarray(close, dtype=float)
    if len(values) < 3:
        raise ValueError("need at least three closes")
    if np.any(values <= 0):
        raise ValueError("closes must be positive")
    return np.diff(np.log(values))


def variance_ratio(close: Sequence[float], q: int) -> float:
    """VR(q) = Var(r^(q)) / (q Var(r)) using the overlapping-window estimators."""
    if q < 2:
        raise ValueError("q must be at least 2")
    r = log_returns(close)
    n = len(r)
    if n < q + 1:
        raise ValueError("series too short for this q")
    mu = float(r.mean())
    demeaned = r - mu
    var1 = float((demeaned ** 2).sum() / (n - 1))
    if var1 <= 0:
        return float("nan")
    window = np.convolve(r, np.ones(q), mode="valid") - q * mu
    m = q * (n - q + 1) * (1.0 - q / n)
    varq = float((window ** 2).sum() / m)
    return varq / var1


def _delta_j(demeaned: np.ndarray, j: int) -> float:
    n = len(demeaned)
    if j >= n:
        return 0.0
    num = float((demeaned[j:] ** 2 * demeaned[:-j] ** 2).sum()) * n
    den = float((demeaned ** 2).sum()) ** 2
    return num / den if den > 0 else 0.0


def variance_ratio_z(close: Sequence[float], q: int, *, robust: bool = True) -> tuple[float, float]:
    """(z, two-sided p). The robust statistic allows for heteroskedastic returns."""
    r = log_returns(close)
    n = len(r)
    vr = variance_ratio(close, q)
    if not np.isfinite(vr):
        return float("nan"), float("nan")
    if not robust:
        variance = 2.0 * (2.0 * q - 1.0) * (q - 1.0) / (3.0 * q * n)
    else:
        demeaned = r - float(r.mean())
        theta = 0.0
        for j in range(1, q):
            weight = (2.0 * (q - j) / q) ** 2
            theta += weight * _delta_j(demeaned, j)
        # Lo and MacKinlay's statistic is sqrt(n q) (VR - 1) -> N(0, theta), so the
        # variance of VR itself is theta / (n q). Under homoskedasticity theta(q) is
        # 2(q-1)(2q-1)/(3q) and this reduces to the homoskedastic variance above.
        variance = theta / (n * q)
    if variance <= 0:
        return float("nan"), float("nan")
    z = (vr - 1.0) / math.sqrt(variance)
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return float(z), float(p)


def variance_ratio_report(close: Sequence[float], params=None) -> dict:
    params = params or load_params()
    qs = [int(q) for q in params.gate.variance_ratio_q]
    primary = int(params.gate.variance_ratio_primary_q)
    robust = str(params.gate.variance_ratio_test) == "lo_mackinlay_robust"
    rows = []
    for q in qs:
        if len(close) < q + 2:
            rows.append({"q": q, "vr": None, "z": None, "p": None, "random_walk": None})
            continue
        vr = variance_ratio(close, q)
        z, p = variance_ratio_z(close, q, robust=robust)
        rows.append({"q": q, "vr": vr, "z": z, "p": p,
                     "random_walk": bool(np.isfinite(p) and p > 0.05)})
    chosen = next((row for row in rows if row["q"] == primary), rows[-1] if rows else None)
    rejected = bool(chosen and chosen["random_walk"] is False)
    return {
        "test": "lo_mackinlay_robust" if robust else "lo_mackinlay_homoskedastic",
        "primary_q": primary,
        "q_values": rows,
        "primary": chosen,
        "rejects_random_walk": rejected,
        "verdict": ("not a random walk at q=%d (p=%.3g)" % (primary, chosen["p"])
                    if rejected and chosen and chosen["p"] is not None
                    else "cannot reject a random walk: the gate treats the symbol as structureless"),
    }


def bounce_gate(levels: Sequence, z: float = 1.96) -> dict:
    """Pooled bounce probability against 0.5, from the level episodes already counted."""
    bounces = sum(float(getattr(level, "bounces", 0.0)) for level in levels)
    penetrations = sum(float(getattr(level, "penetrations", 0.0)) for level in levels)
    resolved = bounces + penetrations
    if resolved <= 0:
        return {"bounces": 0, "penetrations": 0, "resolved": 0, "probability": None,
                "interval": None, "informative": None,
                "verdict": "no resolved level episodes: nothing to test"}
    probability = bounces / resolved
    low, high = wilson_interval(int(bounces), int(resolved), z)
    informative = bool(low > 0.5 or high < 0.5)
    return {
        "bounces": bounces,
        "penetrations": penetrations,
        "resolved": resolved,
        "probability": probability,
        "interval": (low, high),
        "informative": informative,
        "verdict": ("levels carry information on this symbol"
                    if informative else
                    "the 95% interval includes 0.5: levels carry no information here"),
    }


def module_gate(name: str, metrics, *, z: float = 1.96) -> dict:
    """One module's out-of-sample hit rate against its own 1/(1+R) baseline."""
    if metrics is None or not metrics.resolved:
        return {"module": name, "resolved": 0, "win_rate": None, "interval": None,
                "baseline_p0": None, "enabled": False,
                "verdict": "no resolved out-of-sample trades"}
    hit = metrics.wins
    n = metrics.resolved
    low, high = wilson_interval(hit, n, z)
    baseline = metrics.baseline_p0
    enabled = bool(baseline is not None and low > baseline)
    return {
        "module": name,
        "resolved": n,
        "hits": hit,
        "win_rate": metrics.win_rate,
        "interval": (low, high),
        "baseline_p0": baseline,
        "enabled": enabled,
        "verdict": ("lower bound %.3f beats the baseline %.3f" % (low, baseline)
                    if enabled else
                    "lower bound %.3f does not beat the baseline %.3f"
                    % (low, baseline if baseline is not None else float("nan"))),
    }


def symbol_gate(
    *,
    candles: Sequence,
    levels: Sequence | None = None,
    module_metrics: dict | None = None,
    params=None,
) -> dict:
    """The whole gate for one symbol, as the spec orders it, with its reasons."""
    params = params or load_params()
    z = float(params.scoring.wilson_z)
    closes = [float(c.close) for c in candles]
    vr = variance_ratio_report(closes, params)
    bounce = bounce_gate(levels or [], z=z)
    modules = {name: module_gate(name, metrics, z=z)
               for name, metrics in (module_metrics or {}).items()}
    enabled = [name for name, row in modules.items() if row["enabled"]]
    reasons = []
    if not vr["rejects_random_walk"]:
        reasons.append("variance ratio cannot reject a random walk")
    if bounce.get("informative") is False:
        reasons.append("support and resistance levels show no information against 0.5")
    if bounce.get("informative") is None:
        reasons.append("not enough resolved level episodes to test the bounce probability")
    if not enabled:
        reasons.append("no module beats its own baseline out of sample")
    passed = bool(vr["rejects_random_walk"] and bounce.get("informative") and enabled)
    return {
        "variance_ratio": vr,
        "bounce": bounce,
        "modules": modules,
        "enabled_modules": enabled,
        "passed": passed,
        "reasons": reasons,
        "verdict": ("gate passed for modules: " + ", ".join(enabled)) if passed
        else "no setup: " + "; ".join(reasons or ["the gate did not pass"]),
    }
