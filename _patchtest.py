import pathlib
tp = pathlib.Path(r"C:\Users\Hi\trading\tests\test_deriv_parser.py")
t = tp.read_text(encoding="utf-8")
if "canonical_id_maps_to_deriv_symbol" in t:
    print("already patched")
    raise SystemExit(0)
old = '''    async def fake_call(payload: dict) -> dict:
        assert payload["style"] == "candles"
        assert payload["granularity"] == 900
        return {"candles": rows + [forming]}'''
new = '''    async def fake_call(payload: dict) -> dict:
        if "active_symbols" in payload:
            # the adapter resolves a canonical id to Deriv's own symbol first
            return {"active_symbols": [
                {"underlying_symbol": "R_75", "underlying_symbol_name": "Volatility 75 Index",
                 "market": "synthetic_index", "submarket": "random_index", "pip_size": 0.01},
            ]}
        assert payload["style"] == "candles"
        assert payload["granularity"] == 900
        assert payload["ticks_history"] == "R_75"
        return {"candles": rows + [forming]}'''
assert old in t
t = t.replace(old, new, 1)

t += '''

async def test_canonical_id_maps_to_deriv_symbol(monkeypatch):
    """GBPUSD is our id; frxGBPUSD is Deriv's. The adapter must translate."""
    adapter = DerivAdapter(app_id="test")
    seen: dict[str, str] = {}

    async def fake_call(payload: dict) -> dict:
        if "active_symbols" in payload:
            return {"active_symbols": [
                {"underlying_symbol": "frxGBPUSD", "underlying_symbol_name": "GBP/USD",
                 "market": "forex", "submarket": "major_pairs", "pip_size": 0.0001},
            ]}
        seen["ticks_history"] = payload["ticks_history"]
        return {"candles": []}

    monkeypatch.setattr(adapter, "_call", fake_call)
    await adapter.get_candles("GBPUSD", Timeframe.H1, limit=5)
    assert seen["ticks_history"] == "frxGBPUSD"


async def test_symbol_index_is_cached(monkeypatch):
    """The symbol list is one websocket round trip, not one per candle request."""
    adapter = DerivAdapter(app_id="test")
    calls = {"n": 0}

    async def fake_call(payload: dict) -> dict:
        if "active_symbols" in payload:
            calls["n"] += 1
            return {"active_symbols": [
                {"underlying_symbol": "frxEURUSD", "underlying_symbol_name": "EUR/USD",
                 "market": "forex", "submarket": "major_pairs", "pip_size": 0.0001},
            ]}
        return {"candles": []}

    monkeypatch.setattr(adapter, "_call", fake_call)
    await adapter.get_candles("EURUSD", Timeframe.H1, limit=5)
    await adapter.get_candles("EURUSD", Timeframe.H1, limit=5)
    assert calls["n"] == 1
'''
tp.write_text(t, encoding="utf-8", newline="\n")
print("deriv tests patched")