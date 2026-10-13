"""Phase 7: candidate features, the logistic model, calibration and the symbol gate."""

from engine.scoring.calibration import (
    brier_score, calibration_report, expected_calibration_error, reliability_buckets,
)
from engine.scoring.features import FEATURE_NAMES, feature_matrix, features_from_signal
from engine.scoring.gate import (
    bounce_gate, module_gate, symbol_gate, variance_ratio, variance_ratio_report,
    variance_ratio_z,
)
from engine.scoring.model import LogisticModel
from engine.scoring.pipeline import (
    FitResult, RankedCandidate, choose_setup, fit_and_calibrate, rank_candidates,
    similar_cases, test_calibration,
)
from engine.scoring.stats import (
    baseline_hit_probability, beta_posterior_mean, expected_value, wilson_interval,
)

__all__ = [
    "brier_score", "calibration_report", "expected_calibration_error", "reliability_buckets",
    "FEATURE_NAMES", "feature_matrix", "features_from_signal",
    "bounce_gate", "module_gate", "symbol_gate", "variance_ratio", "variance_ratio_report",
    "variance_ratio_z",
    "LogisticModel",
    "FitResult", "RankedCandidate", "choose_setup", "fit_and_calibrate", "rank_candidates",
    "similar_cases", "test_calibration",
    "baseline_hit_probability", "beta_posterior_mean", "expected_value", "wilson_interval",
]
