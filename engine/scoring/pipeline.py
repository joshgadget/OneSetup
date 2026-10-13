"""From labelled history to one decision: setup, or no setup.

The spec's pipeline: fetch closed candles for several timeframes, run every module,
apply the gate, score the survivors with a model trained on past outcomes, and return
the single best candidate with its entry, stop, target, reasons, invalidation and the
evidence behind its number - or return "no setup", which is a valid answer.

Three rules are enforced here and none of them can be overridden by a caller:

* a number is only called a probability when the model was trained on a past period and
  its reliability buckets line up on a later period the model never saw;
* a setup is only shown when EV is positive, at least scoring.min_similar_cases similar
  historical cases exist, and the lower end of the 95% interval on their hit rate clears
  the candidate's own baseline p0 = 1/(1+R);
* expired and unfilled trades never enter the hit rate; they are counted and reported.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from engine.config import load_params
from engine.models import Candle, ModuleSignal
from engine.scoring import calibration as calibration_module
from engine.scoring.features import (
    FEATURE_NAMES, SIMILARITY_FEATURES, feature_matrix, target_vector,
)
from engine.scoring.model import LogisticModel
from engine.scoring.stats import baseline_hit_probability, expected_value, wilson_interval


@dataclass
class FitResult:
    model: LogisticModel | None
    train_cases: int
    validation: dict | None
    test: dict | None
    notes: tuple[str, ...] = ()

    @property
    def calibrated(self) -> bool:
        return bool(self.validation and self.validation.get("calibrated"))

    def to_dict(self) -> dict:
        return {
            "trained": self.model is not None,
            "train_cases": self.train_cases,
            "validation": self.validation,
            "test": self.test,
            "calibrated": self.calibrated,
            "notes": list(self.notes),
        }


def _resolved(trades: Sequence) -> list:
    return [t for t in trades if t.outcome in ("target", "stop")]


def fit_and_calibrate(train_trades: Sequence, validation_trades: Sequence, params=None) -> FitResult:
    """Fit on the training period, then check the buckets on a later period."""
    params = params or load_params()
    scoring = params.scoring
    train = _resolved(train_trades)
    validation = _resolved(validation_trades)
    min_cases = int(scoring.min_train_cases)
    notes: list[str] = []
    if len(train) < min_cases:
        notes.append(f"only {len(train)} resolved training trades, below the "
                     f"{min_cases} the parameters require: no model fitted")
        return FitResult(None, len(train), None, None, tuple(notes))
    signals = [t.signal for t in train]
    X, missing = feature_matrix(signals, FEATURE_NAMES, params)
    y = target_vector(train)
    if len(set(y.tolist())) < 2:
        notes.append("training period has only one outcome class: no model fitted")
        return FitResult(None, len(train), None, None, tuple(notes))
    model = LogisticModel.fit(
        X, y, FEATURE_NAMES,
        l2=float(scoring.l2_penalty), iterations=int(scoring.max_iterations),
        missing=missing,
    )
    validation_report = None
    if validation:
        Xv, _ = feature_matrix([t.signal for t in validation], FEATURE_NAMES, params)
        pv = model.predict_proba(Xv)
        yv = target_vector(validation)
        validation_report = calibration_module.calibration_report(
            yv, pv, buckets=int(scoring.calibration_buckets),
            ece_tolerance=float(scoring.calibration_ece_tolerance),
            min_counts=int(scoring.calibration_min_cases),
        )
    else:
        notes.append("no resolved validation trades: the number stays a score")
    return FitResult(model, len(train), validation_report, None, tuple(notes))


def test_calibration(fit: FitResult, test_trades: Sequence, params=None) -> dict | None:
    """The one look at the test period. Reported, never used to refit."""
    if fit.model is None:
        return None
    params = params or load_params()
    rows = _resolved(test_trades)
    if not rows:
        return None
    X, _ = feature_matrix([t.signal for t in rows], FEATURE_NAMES, params)
    p = fit.model.predict_proba(X)
    y = target_vector(rows)
    return calibration_module.calibration_report(
        y, p, buckets=int(params.scoring.calibration_buckets),
        ece_tolerance=float(params.scoring.calibration_ece_tolerance),
        min_counts=int(params.scoring.calibration_min_cases),
    )


def similarity_space(history: Sequence, params=None) -> dict:
    """Means, scales and the standardised history matrix of the similarity subset.

    The matrix is computed once and carried in the space, so scoring many candidates
    against the same history does not recompute the history features per candidate.
    """
    params = params or load_params()
    rows = _resolved(history)
    if not rows:
        raise ValueError("no resolved history to measure similarity against")
    X, _ = feature_matrix([t.signal for t in rows], SIMILARITY_FEATURES, params)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    means = X.mean(axis=0)
    scales = X.std(axis=0)
    scales[scales <= 1e-12] = 1.0
    return {"means": means, "scales": scales, "rows": rows,
            "Z": (X - means) / scales, "dimensions": len(SIMILARITY_FEATURES)}


def _similar_from_vector(x: np.ndarray, space: dict, params) -> dict:
    """The neighbour count for one candidate feature vector inside a prepared space."""
    radius = float(params.scoring.similar_radius) * float(np.sqrt(space["dimensions"]))
    z = (np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0) - space["means"]) / space["scales"]
    distances = np.sqrt(((space["Z"] - z[None, :]) ** 2).sum(axis=1))
    mask = distances <= radius
    n = int(mask.sum())
    if n == 0:
        return {"cases": 0, "hits": 0, "hit_rate": None, "interval": None, "radius": radius}
    hits = [t for t, keep in zip(space["rows"], mask) if keep and t.outcome == "target"]
    low, high = wilson_interval(len(hits), n, float(params.scoring.wilson_z))
    return {"cases": n, "hits": len(hits), "hit_rate": len(hits) / n,
            "interval": (low, high), "radius": radius}


def similar_cases(
    candidate: ModuleSignal,
    history: Sequence,
    model: LogisticModel | None = None,
    params=None,
    *,
    space: dict | None = None,
) -> dict:
    """Past resolved cases inside scoring.similar_radius standard deviations per dimension.

    The radius is measured per dimension and scaled by sqrt(d), so "similar" means the
    same thing whatever the number of features is. It is a reading of "similar" that the
    spec leaves open (DECISIONS.md D-015). Pass a prepared `space` when scoring many
    candidates against the same history.
    """
    params = params or load_params()
    rows = _resolved(history)
    if not rows:
        return {"cases": 0, "hits": 0, "hit_rate": None, "interval": None, "radius": None}
    space = space or similarity_space(rows, params)
    x = feature_matrix([candidate], SIMILARITY_FEATURES, params)[0][0]
    return _similar_from_vector(x, space, params)


@dataclass
class RankedCandidate:
    signal: ModuleSignal
    reward_risk: float
    baseline_p0: float
    probability: float | None
    score: float | None
    expected_value: float | None
    similar: dict
    cost_r: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "module": self.signal.module_name,
            "module_version": self.signal.module_version,
            "direction": self.signal.direction.value,
            "entry": self.signal.entry,
            "stop": self.signal.stop,
            "target": self.signal.target,
            "known_time": self.signal.known_time.isoformat(),
            "formation_time": self.signal.formation_time.isoformat(),
            "reward_risk": self.reward_risk,
            "baseline_p0": self.baseline_p0,
            "probability": self.probability,
            "score": self.score,
            "expected_value": self.expected_value,
            "similar": self.similar,
            "cost_r": self.cost_r,
            "reasons": list(self.signal.reasons),
            "invalidation": list(self.signal.invalidation),
            "notes": list(self.notes),
        }


def rank_candidates(
    candidates: Sequence[ModuleSignal],
    history: Sequence,
    fit: FitResult,
    *,
    cost_r: float = 0.0,
    params=None,
) -> list[RankedCandidate]:
    """Score and rank survivors by expected value, never by raw probability."""
    params = params or load_params()
    scored: list[RankedCandidate] = []
    probabilities = None
    X_sim = None
    space = None
    if fit.model is not None and candidates:
        X, _ = feature_matrix(list(candidates), FEATURE_NAMES, params)
        probabilities = fit.model.predict_proba(X)
        X_sim, _ = feature_matrix(list(candidates), SIMILARITY_FEATURES, params)
        if history:
            try:
                space = similarity_space(history, params)
            except ValueError:
                space = None
    for i, candidate in enumerate(candidates):
        risk = abs(candidate.entry - candidate.stop)
        if risk <= 0:
            continue
        reward_risk = abs(candidate.target - candidate.entry) / risk
        p0 = baseline_hit_probability(reward_risk)
        probability = float(probabilities[i]) if probabilities is not None else None
        if probability is None or not fit.calibrated:
            probability = None
        score = float(probabilities[i]) if probabilities is not None else None
        ev = expected_value(probability, reward_risk, cost_r) if probability is not None else None
        scored.append(RankedCandidate(
            signal=candidate, reward_risk=reward_risk, baseline_p0=p0,
            probability=probability, score=score, expected_value=ev,
            similar=(_similar_from_vector(X_sim[i], space, params)
                     if X_sim is not None and space is not None else
                     {"cases": 0, "hits": 0, "hit_rate": None, "interval": None}),
            cost_r=cost_r,
        ))
    scored.sort(key=lambda row: (row.expected_value if row.expected_value is not None else -9e9),
                reverse=True)
    return scored


def choose_setup(ranked: Sequence[RankedCandidate], *, gate: dict | None = None, params=None) -> dict:
    """The show rule from the spec: EV > 0, enough similar cases, interval clears p0."""
    params = params or load_params()
    scoring = params.scoring
    if gate is not None and not gate.get("passed", False):
        return {
            "status": "no_setup",
            "reason": gate.get("verdict", "the symbol gate did not pass"),
            "gate": gate,
        }
    if not ranked:
        return {"status": "no_setup", "reason": "no candidate setup was produced"}
    best = ranked[0]
    if best.probability is None:
        return {
            "status": "no_setup",
            "reason": "the model is not calibrated on this symbol, so no probability and "
                      "no expected value can be quoted",
            "score": best.score,
            "candidate": best.to_dict(),
        }
    if best.expected_value is None or best.expected_value <= float(scoring.ev_min):
        return {
            "status": "no_setup",
            "reason": f"expected value {best.expected_value:.3f} R is not above "
                      f"{scoring.ev_min}",
            "candidate": best.to_dict(),
        }
    cases = best.similar.get("cases") or 0
    if cases < int(scoring.min_similar_cases):
        return {
            "status": "no_setup",
            "reason": f"only {cases} similar past cases, below the "
                      f"{scoring.min_similar_cases} required",
            "candidate": best.to_dict(),
        }
    low = (best.similar.get("interval") or (None, None))[0]
    if low is None or low <= best.baseline_p0:
        return {
            "status": "no_setup",
            "reason": f"the 95% interval's lower bound on similar cases "
                      f"({low if low is not None else float('nan'):.3f}) does not clear the "
                      f"baseline {best.baseline_p0:.3f}",
            "candidate": best.to_dict(),
        }
    return {
        "status": "setup",
        "candidate": best.to_dict(),
        "probability": best.probability,
        "interval": best.similar["interval"],
        "similar_cases": cases,
        "baseline_p0": best.baseline_p0,
        "expected_value": best.expected_value,
        "statement": (f"estimated p = {best.probability:.2f} "
                      f"(95% interval {best.similar['interval'][0]:.2f} to "
                      f"{best.similar['interval'][1]:.2f}), from {cases} similar past setups"),
    }
