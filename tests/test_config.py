from engine.config import load_params


def test_params_load_and_hash():
    p = load_params()
    assert p.version == "params.v1"
    assert len(p.file_hash) == 64


def test_spec_defaults_present():
    p = load_params()
    assert p.foundation.atr_period == 14
    assert p.foundation.pivot.k_default == 3
    assert p.support_resistance.break_buffer_atr == 0.10
    assert p.support_resistance.horizon_omega == 5
    assert p.supply_demand.leg_out_atr == 2.0
    assert p.liquidity.grab_buffer_atr == 0.05
    assert p.scoring.wilson_z == 1.96
    assert p.scoring.min_similar_cases == 100


def test_dotted_access_and_markets():
    p = load_params()
    assert p.get_path("markets.forex.sessions") is True
    assert p.get_path("markets.synthetic.sessions") is False
    assert p.get_path("does.not.exist", default="fallback") == "fallback"