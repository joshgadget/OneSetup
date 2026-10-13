"""Logistic model on the candidate features, fitted with numpy only.

The spec's likelihood is p = sigmoid(w^T x), fitted on a training period and checked on
a later period the model never saw. This is an iteratively reweighted least squares fit
with an L2 penalty, standardised inputs, and a ridge fallback if the normal equations
are ill-conditioned. Nothing here reports a probability: that decision belongs to
engine.scoring.calibration, which is allowed to fail.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


def _sigmoid(z: np.ndarray) -> np.ndarray:
    out = np.empty_like(z, dtype=float)
    positive = z >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-z[positive]))
    exp_z = np.exp(z[~positive])
    out[~positive] = exp_z / (1.0 + exp_z)
    return out


@dataclass
class LogisticModel:
    names: tuple[str, ...]
    weights: np.ndarray
    bias: float
    means: np.ndarray
    scales: np.ndarray
    l2: float = 1.0
    iterations: int = 50
    trained_on: int = 0
    positives: int = 0
    missing: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------ maths

    def _standardise(self, X: np.ndarray) -> np.ndarray:
        Z = (X - self.means) / self.scales
        return np.nan_to_num(Z, nan=0.0, posinf=0.0, neginf=0.0)

    def decision(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=float))
        return self._standardise(X) @ self.weights + self.bias

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return _sigmoid(self.decision(X))

    def to_dict(self) -> dict:
        return {
            "names": list(self.names),
            "weights": [float(w) for w in self.weights],
            "bias": float(self.bias),
            "means": [float(m) for m in self.means],
            "scales": [float(s) for s in self.scales],
            "l2": float(self.l2),
            "iterations": int(self.iterations),
            "trained_on": int(self.trained_on),
            "positives": int(self.positives),
            "missing": dict(self.missing),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LogisticModel":
        return cls(
            names=tuple(data["names"]),
            weights=np.array(data["weights"], dtype=float),
            bias=float(data["bias"]),
            means=np.array(data["means"], dtype=float),
            scales=np.array(data["scales"], dtype=float),
            l2=float(data.get("l2", 1.0)),
            iterations=int(data.get("iterations", 0)),
            trained_on=int(data.get("trained_on", 0)),
            positives=int(data.get("positives", 0)),
            missing=dict(data.get("missing", {})),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "LogisticModel":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # ------------------------------------------------------------------- fit

    @classmethod
    def fit(
        cls,
        X: np.ndarray,
        y: np.ndarray,
        names: tuple[str, ...],
        *,
        l2: float = 1.0,
        iterations: int = 50,
        missing: dict[str, int] | None = None,
    ) -> "LogisticModel":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        if X.ndim != 2 or len(X) != len(y):
            raise ValueError("X must be (n, d) and match y")
        if len(X) == 0:
            raise ValueError("cannot fit a model on zero rows")
        if len(set(y.tolist())) < 2:
            raise ValueError("cannot fit a model when every case has the same label")
        # features can be missing in every row for a given module: treat that column as
        # a constant zero rather than letting numpy average an empty slice
        counts = (~np.isnan(X)).sum(axis=0)
        safe = np.where(np.isnan(X), 0.0, X)
        means = np.divide(safe.sum(axis=0), counts, out=np.zeros(X.shape[1]),
                          where=counts > 0)
        squared = np.where(np.isnan(X), 0.0, (X - means) ** 2)
        variances = np.divide(squared.sum(axis=0), counts, out=np.zeros(X.shape[1]),
                              where=counts > 0)
        scales = np.sqrt(variances)
        scales[scales <= 1e-12] = 1.0
        Z = np.nan_to_num((X - means) / scales, nan=0.0, posinf=0.0, neginf=0.0)

        n, d = Z.shape
        design = np.hstack([Z, np.ones((n, 1))])
        w = np.zeros(d + 1, dtype=float)
        penalty = np.eye(d + 1) * float(l2)
        penalty[-1, -1] = 0.0            # never penalise the intercept
        for step in range(int(iterations)):
            p = _sigmoid(design @ w)
            weights = np.clip(p * (1.0 - p), 1e-9, None)
            gradient = design.T @ (p - y) + penalty @ w
            hessian = design.T @ (design * weights[:, None]) + penalty
            try:
                delta = np.linalg.solve(hessian, gradient)
            except np.linalg.LinAlgError:
                delta, *_ = np.linalg.lstsq(hessian, gradient, rcond=None)
            w = w - delta
            if float(np.max(np.abs(delta))) < 1e-9:
                break
        return cls(
            names=tuple(names), weights=w[:-1], bias=float(w[-1]),
            means=means, scales=scales, l2=float(l2), iterations=step + 1,
            trained_on=int(n), positives=int(y.sum()), missing=dict(missing or {}),
        )
