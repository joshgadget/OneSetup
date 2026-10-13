"""Phase 7 tests: the logistic model, the calibration verdict and the show rule.

The synthetic data in these tests has a known structure: the outcome is a logistic
function of one feature, so a correct model has to find it, and a correct calibration
check has to pass it. Anything that "predicts" better than that is a bug, not a result.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from engine.backtest.metrics import compute_metrics
from engine.config import load_params
from engine.models import Candle, Direction, Timeframe
from engine.modules.common import make_signal
from engine.scoring.calibration import brier_score, calibration_report
from engine.scoring.gate import bounce_gate, module_gate, variance_ratio, variance_ratio_report, variance_ratio_z
from engine.scoring.model import LogisticModel
from engine.scoring.pipeline import choose_setup, fit_and_calibrate, rank_candidates, similar_cases

params = load_params()
T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def a_candle(i: int = 0) -> Candle:
    return Candle(symbol="TEST", timeframe=Timeframe.H1, open_time=T0 + timedelta(hours=i),
                  open=1.1, high=1.1005, low=1.0995, close=1.1)


def synthetic_case(i: int, driver: float, *, reward_risk: float = 2.0, module: str = "msnr",
                   entry: float = 1.1000):
    risk = entry * 0.0005
    candle = a_candle(i)
    signal = make_signal(
        module=module, version="0.0.0", symbol="TEST", timeframe=Timeframe.H1,
        direction=Direction.LONG, entry=entry, stop=entry - risk,
        target=entry + risk * reward_risk,
        candle=candle, known_index=0, candles=[candle],
        features={"atr": risk, "grab_depth_atr": driver, "zone_score": 0.5,
                  "pool_size": 2.0, "grab_rejection": 0.4},
    )
    return signal


def make_trades(n: int, seed: int, *, strength: float = 1.6, reward_risk: float = 2.0):
    rng = np.random.default_rng(seed)
    from engine.backtest.simulator import Trade

    out = []
    for i in range(n):
        driver = float(rng.normal(0.0, 1.0))
        p = float(sigmoid(strength * driver))
        hit = rng.random() < p
        signal = synthetic_case(i, driver, reward_risk=reward_risk)
        risk = signal.entry - signal.stop
        trade = Trade(
            signal=signal, direction=Direction.LONG, entry=signal.entry, stop=signal.stop,
            target=signal.target, risk_distance=risk, reward_risk=reward_risk,
            known_index=0, fill_index=1, fill_price=signal.entry, exit_index=2,
            exit_price=signal.target if hit else signal.stop,
            outcome="target" if hit else "stop",
            r_gross=reward_risk if hit else -1.0,
            r_net=(reward_risk if hit else -1.0) - 0.05,
            bars_in_trade=5,
        )
        out.append(trade)
    return out


# ------------------------------------------------------------------ the model

def test_logistic_fit_finds_the_direction_of_a_real_signal():
    trades = make_trades(1200, seed=1)
    from engine.scoring.features import FEATURE_NAMES, feature_matrix, target_vector

    X, missing = feature_matrix([t.signal for t in trades], FEATURE_NAMES, params)
    y = target_vector(trades)
    model = LogisticModel.fit(X, y, FEATURE_NAMES, l2=1.0, iterations=50, missing=missing)
    weight = float(model.weights[FEATURE_NAMES.index("grab_depth_atr")])
    assert weight > 0.2, "the driver feature must carry a positive weight"
    p = model.predict_proba(X)
    assert np.all((p > 0) & (p < 1))
    driver = X[:, FEATURE_NAMES.index("grab_depth_atr")]
    assert p[driver > 1.0].mean() > 0.7, "high driver cases must be predicted as winners"
    assert p[driver < -1.0].mean() < 0.3, "low driver cases must be predicted as losers"


def test_model_refuses_degenerate_training_sets():
    X = np.zeros((5, 3))
    with pytest.raises(ValueError):
        LogisticModel.fit(X, np.ones(5), ("a", "b", "c"))
    with pytest.raises(ValueError):
        LogisticModel.fit(np.zeros((0, 3)), np.zeros(0), ("a", "b", "c"))


def test_model_round_trips_through_a_file(tmp_path):
    trades = make_trades(200, seed=2)
    from engine.scoring.features import FEATURE_NAMES, feature_matrix, target_vector

    X, _ = feature_matrix([t.signal for t in trades], FEATURE_NAMES, params)
    y = target_vector(trades)
    model = LogisticModel.fit(X, y, FEATURE_NAMES)
    path = tmp_path / "model.json"
    model.save(path)
    restored = LogisticModel.load(path)
    assert np.allclose(model.predict_proba(X), restored.predict_proba(X))


# ------------------------------------------------------------- calibration

def test_calibration_passes_a_well_specified_model():
    trades = make_trades(1800, seed=3)
    from engine.scoring.features import FEATURE_NAMES, feature_matrix, target_vector

    X, _ = feature_matrix([t.signal for t in trades], FEATURE_NAMES, params)
    y = target_vector(trades)
    model = LogisticModel.fit(X, y, FEATURE_NAMES)
    report = calibration_report(y, model.predict_proba(X), buckets=10, ece_tolerance=0.05,
                                min_counts=500)
    assert report["enough_cases"] is True
    assert report["ece"] < 0.05
    assert report["calibrated"] is True
    assert report["verdict"] == "probability"
    assert report["brier"] < 0.25


def test_calibration_flags_an_overconfident_model_as_a_score():
    trades = make_trades(1800, seed=4)
    from engine.scoring.features import FEATURE_NAMES, feature_matrix, target_vector

    X, _ = feature_matrix([t.signal for t in trades], FEATURE_NAMES, params)
    y = target_vector(trades)
    overconfident = sigmoid(4.0 * X[:, FEATURE_NAMES.index("grab_depth_atr")])
    report = calibration_report(y, overconfident, buckets=10, ece_tolerance=0.05,
                                min_counts=500)
    assert report["calibrated"] is False
    assert report["verdict"] == "score", "a model that does not line up is a score"
    assert report["ece"] > 0.05


def test_calibration_refuses_a_verdict_on_a_small_sample():
    y = [1, 0] * 5
    p = [0.6] * 10
    report = calibration_report(y, p)
    assert report["calibrated"] is False and report["verdict"] == "score"
    with pytest.raises(ValueError):
        brier_score([1, 0], [0.5])


# ------------------------------------------------------------------- the gate

def test_variance_ratio_is_one_on_a_random_walk():
    rng = np.random.default_rng(19)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 4000)))
    vr = variance_ratio(close, 4)
    assert abs(vr - 1.0) < 0.15
    z, p = variance_ratio_z(close, 4, robust=True)
    assert p > 0.05, "a random walk must not be rejected (a false positive here is a bug)"
    report = variance_ratio_report(close, params)
    assert report["rejects_random_walk"] is False
    assert "cannot reject a random walk" in report["verdict"]
    assert report["primary_q"] == int(params.gate.variance_ratio_primary_q)
    assert len(report["q_values"]) == len(params.gate.variance_ratio_q)


def test_variance_ratio_detects_persistence_and_mean_reversion():
    rng = np.random.default_rng(6)
    n = 4000
    persistent = np.zeros(n)
    reverting = np.zeros(n)
    for i in range(1, n):
        persistent[i] = 0.35 * persistent[i - 1] + rng.normal(0, 0.01)
        reverting[i] = -0.35 * reverting[i - 1] + rng.normal(0, 0.01)
    trending = 100 * np.exp(np.cumsum(persistent))
    choppy = 100 * np.exp(np.cumsum(reverting))
    assert variance_ratio(trending, 4) > 1.2
    assert variance_ratio(choppy, 4) < 0.8
    z_up, p_up = variance_ratio_z(trending, 4)
    z_down, _ = variance_ratio_z(choppy, 4)
    assert p_up < 0.01 and z_up > 0 and z_down < 0


class FakeLevel:
    def __init__(self, bounces, penetrations):
        self.bounces = bounces
        self.penetrations = penetrations


def test_bounce_gate_tests_the_interval_against_one_half():
    informative = bounce_gate([FakeLevel(60, 40), FakeLevel(30, 20)])
    assert informative["probability"] == pytest.approx(0.6)
    assert informative["informative"] is True
    flat = bounce_gate([FakeLevel(52, 48)])
    assert flat["informative"] is False
    assert "includes 0.5" in flat["verdict"]
    empty = bounce_gate([])
    assert empty["informative"] is None


def test_module_gate_uses_the_wilson_lower_bound_against_p0():
    class T:
        def __init__(self, r_net, outcome):
            self.r_net = r_net
            self.outcome = outcome
            self.entry, self.stop, self.target = 100.0, 99.0, 102.0
            self.exit_index = 0
            self.bars_in_trade = 1
            self.cost_r = 0.0
            self.funding_r = 0.0
            self.ambiguous = False

    good = [T(2.0, "target") for _ in range(60)] + [T(-1.0, "stop") for _ in range(40)]
    row = module_gate("msnr", compute_metrics(good))
    assert row["enabled"] is True and row["interval"][0] > row["baseline_p0"]
    weak = [T(2.0, "target") for _ in range(35)] + [T(-1.0, "stop") for _ in range(65)]
    assert module_gate("msnr", compute_metrics(weak))["enabled"] is False
    assert module_gate("msnr", None)["enabled"] is False


# ------------------------------------------------------------------ the rules

def test_fit_and_calibrate_needs_enough_cases_and_a_later_period():
    short = make_trades(30, seed=7)
    fit = fit_and_calibrate(short, short, params)
    assert fit.model is None and fit.calibrated is False
    assert any("below the" in note for note in fit.notes)

    trades = make_trades(2700, seed=8)
    fit = fit_and_calibrate(trades[:1800], trades[1800:2700], params)
    assert fit.model is not None
    assert fit.validation is not None
    assert fit.model.trained_on == 1800
    assert fit.validation["cases"] >= int(params.scoring.calibration_min_cases)
    assert fit.calibrated is True


def test_end_to_end_ranking_shows_a_setup_with_a_statement():
    history = make_trades(2700, seed=9)
    fit = fit_and_calibrate(history[:1800], history[1800:2700], params)
    assert fit.calibrated
    candidates = [synthetic_case(0, 2.0), synthetic_case(1, -2.0)]
    ranked = rank_candidates(candidates, history, fit, params=params)
    assert ranked[0].signal is candidates[0], "the strongest driver ranks first"
    assert ranked[0].probability is not None and ranked[0].expected_value > 0
    decision = choose_setup(ranked, params=params)
    assert decision["status"] == "setup"
    assert "estimated p = " in decision["statement"]
    assert "similar past setups" in decision["statement"]
    assert decision["similar_cases"] >= int(params.scoring.min_similar_cases)


def test_choose_setup_refuses_every_unproven_case():
    history = make_trades(2700, seed=10)
    fit = fit_and_calibrate(history[:1800], history[1800:2700], params)
    ranked = rank_candidates([synthetic_case(0, 2.0)], history, fit, params=params)
    assert choose_setup(ranked, params=params)["status"] == "setup"

    gate = {"passed": False, "verdict": "no setup: variance ratio cannot reject a random walk"}
    blocked = choose_setup(ranked, gate=gate, params=params)
    assert blocked["status"] == "no_setup" and "random walk" in blocked["reason"]

    assert choose_setup([], params=params)["status"] == "no_setup"

    uncalibrated = fit_and_calibrate(make_trades(2700, seed=10), [], params)
    weak = rank_candidates([synthetic_case(0, 2.0)], history, uncalibrated, params=params)
    assert weak[0].probability is None and weak[0].expected_value is None
    assert choose_setup(weak, params=params)["status"] == "no_setup"

    far = rank_candidates([synthetic_case(0, 9.0)], history, fit, params=params)
    assert far[0].similar["cases"] == 0
    assert "similar past cases" in choose_setup(far, params=params)["reason"]

    from engine.scoring.pipeline import RankedCandidate

    losing = RankedCandidate(
        signal=synthetic_case(0, 0.0), reward_risk=2.0, baseline_p0=1 / 3,
        probability=0.2, score=0.2, expected_value=-0.6, cost_r=0.0,
        similar={"cases": 500, "hits": 250, "hit_rate": 0.5, "interval": (0.45, 0.55)},
    )
    assert "expected value" in choose_setup([losing], params=params)["reason"]

    below_baseline = RankedCandidate(
        signal=synthetic_case(0, 0.0), reward_risk=2.0, baseline_p0=1 / 3,
        probability=0.4, score=0.4, expected_value=0.2, cost_r=0.0,
        similar={"cases": 500, "hits": 150, "hit_rate": 0.3, "interval": (0.26, 0.34)},
    )
    reason = choose_setup([below_baseline], params=params)["reason"]
    assert "does not clear the baseline" in reason


def test_similar_cases_uses_a_dimension_scaled_radius():
    history = make_trades(600, seed=11)
    near = similar_cases(synthetic_case(0, 0.5), history, None, params)
    far = similar_cases(synthetic_case(0, 12.0), history, None, params)
    assert near["cases"] > far["cases"]
    assert near["cases"] >= int(params.scoring.min_similar_cases)
    assert 0.0 <= near["interval"][0] <= near["interval"][1] <= 1.0
