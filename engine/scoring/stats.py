"""Interval and posterior maths shared by the gate, the metrics and the ranking.

The spec asks for exactly two of these. The Wilson interval, validation section:

    ( p + z^2/2n +/- z*sqrt( p(1-p)/n + z^2/4n^2 ) ) / (1 + z^2/n),  z = 1.96

and the Bayesian bounce probability, support and resistance section:

    E[p] = (n+1)/(N+2),  Var[p] = (n+1)(N-n+1) / ((N+3)(N+2)^2)
"""
from __future__ import annotations

import math

Z_95 = 1.96


def wilson_interval(k: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials. n = 0 gives (0, 1)."""
    if n <= 0:
        return 0.0, 1.0
    if k < 0 or k > n:
        raise ValueError(f"k={k} outside [0, {n}]")
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = p + z2 / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, (centre - spread) / denom), min(1.0, (centre + spread) / denom)


def wilson_lower_bound(k: int, n: int, z: float = Z_95) -> float:
    return wilson_interval(k, n, z)[0]


def beta_posterior_mean(bounces: float, penetrations: float) -> float:
    """E[p] = (n+1)/(N+2) with a uniform prior. No history gives exactly 0.5."""
    n = float(bounces)
    total = float(bounces) + float(penetrations)
    return (n + 1.0) / (total + 2.0)


def beta_posterior_variance(bounces: float, penetrations: float) -> float:
    """Var[p] = (n+1)(N-n+1) / ((N+3)(N+2)^2)."""
    n = float(bounces)
    total = float(bounces) + float(penetrations)
    if total < 0:
        raise ValueError("negative counts")
    return ((n + 1.0) * (total - n + 1.0)) / ((total + 3.0) * (total + 2.0) ** 2)


def baseline_hit_probability(reward_risk: float) -> float:
    """p0 = 1/(1+R): the driftless random-walk chance the target comes first."""
    if reward_risk < 0:
        raise ValueError("reward/risk must be non-negative")
    return 1.0 / (1.0 + reward_risk)


def expected_value(probability: float, reward_risk: float, cost_r: float = 0.0) -> float:
    """EV = p*R - (1-p) - c, in units of risk."""
    return probability * reward_risk - (1.0 - probability) - cost_r