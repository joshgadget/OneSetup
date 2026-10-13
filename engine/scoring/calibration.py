"""Is the number a probability or just a score? This file decides.

The spec: bucket the predictions, compare each bucket's average prediction to its
observed hit rate, and report the Brier score. If the buckets do not line up, the number
is a score and the interface must not call it a probability.

The rule used here (a reading of "do not line up", DECISIONS.md D-015): the expected
calibration error, the count-weighted mean gap between average prediction and observed
rate across buckets, must be at or below scoring.calibration_ece_tolerance. Every bucket
is returned whether or not it passes.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np


def reliability_buckets(y: Sequence[float], p: Sequence[float], *, buckets: int = 10) -> list[dict]:
    """Equal-width buckets over [0, 1]: average prediction against observed rate."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    if len(y) != len(p):
        raise ValueError("y and p must be the same length")
    edges = np.linspace(0.0, 1.0, buckets + 1)
    out = []
    for i in range(buckets):
        low, high = float(edges[i]), float(edges[i + 1])
        last = i == buckets - 1
        mask = (p >= low) & (p <= high) if last else (p >= low) & (p < high)
        count = int(mask.sum())
        if count == 0:
            out.append({"low": low, "high": high, "count": 0,
                        "mean_prediction": None, "observed_rate": None, "gap": None})
            continue
        mean_prediction = float(p[mask].mean())
        observed = float(y[mask].mean())
        out.append({"low": low, "high": high, "count": count,
                    "mean_prediction": mean_prediction, "observed_rate": observed,
                    "gap": abs(mean_prediction - observed)})
    return out


def brier_score(y: Sequence[float], p: Sequence[float]) -> float:
    """Brier = mean((p - y)^2). Lower is better; 0.25 is the coin-flip reference at p=0.5."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    if len(y) != len(p):
        raise ValueError("y and p must be the same length")
    if len(y) == 0:
        return float("nan")
    return float(np.mean((p - y) ** 2))


def expected_calibration_error(buckets: Sequence[dict]) -> float:
    total = sum(b["count"] for b in buckets)
    if total == 0:
        return float("nan")
    return float(sum(b["count"] * b["gap"] for b in buckets if b["count"]) / total)


def calibration_report(
    y: Sequence[float],
    p: Sequence[float],
    *,
    buckets: int = 10,
    ece_tolerance: float = 0.05,
    min_counts: int = 500,
) -> dict:
    """Buckets, Brier, ECE, and the verdict the interface has to respect.

    The verdict needs enough cases as well as small buckets. With a few hundred
    validation trades the expected calibration error of a *correct* model is around
    0.05 to 0.08 by sampling noise alone, so a smaller sample reports a score rather
    than claiming a probability it cannot stand behind.
    """
    rows = reliability_buckets(y, p, buckets=buckets)
    observed_total = int(sum(b["count"] for b in rows))
    ece = expected_calibration_error(rows)
    enough = observed_total >= min_counts
    calibrated = bool(enough and np.isfinite(ece) and ece <= float(ece_tolerance))
    return {
        "buckets": rows,
        "brier": brier_score(y, p),
        "ece": ece,
        "cases": observed_total,
        "enough_cases": enough,
        "calibrated": calibrated,
        "tolerance": float(ece_tolerance),
        "verdict": ("probability" if calibrated else "score"),
    }
