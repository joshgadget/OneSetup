import pathlib
f = pathlib.Path(r"C:\Users\Hi\trading\engine\modules\support_resistance.py")
t = f.read_text(encoding="utf-8")

old = '''def discover_levels(
    candles: Sequence[Candle],
    foundation: Foundation,
    params,
    *,
    collect_signals: bool = True,
) -> tuple[list[Level], list]:
    """Build the level set online and count evidence as the series unfolds."""'''
new = '''def discover_levels(
    candles: Sequence[Candle],
    foundation: Foundation,
    params,
    *,
    collect_signals: bool = True,
    stop_index: int | None = None,
) -> tuple[list[Level], list]:
    """Build the level set online and count evidence as the series unfolds.

    `stop_index` runs the scan but stops the clock there. It exists so a test can prove
    that a longer series produces exactly the same state at candle t as a truncated
    series does: the scan must not touch a candle after t.
    """'''
assert old in t
t = t.replace(old, new, 1)

old = '''    for t, candle in enumerate(candles):
        gamma = gammas[t]
        atr = foundation.atr_at(t)'''
new = '''    for t, candle in enumerate(candles):
        if stop_index is not None and t > stop_index:
            break
        gamma = gammas[t]
        atr = foundation.atr_at(t)'''
assert old in t
t = t.replace(old, new, 1)
f.write_text(t, encoding="utf-8", newline="\n")

tp = pathlib.Path(r"C:\Users\Hi\trading\tests\test_support_resistance.py")
tt = tp.read_text(encoding="utf-8")
tt = tt.replace('''def drive(level: Level, path: list[float], atr: float = 20.0, buffer: float = 0.1,
          horizon: int = HORIZON) -> int:
    """Push a level through a close path. Returns the number of signal triggers.

    atr defaults to 20 so the break check cannot fire and the episode logic is isolated.
    """''', '''def drive(level: Level, path: list[float], atr: float = 200.0, buffer: float = 0.1,
          horizon: int = HORIZON) -> int:
    """Push a level through a close path. Returns the number of signal triggers.

    atr defaults to 200, so the break buffer is 20 and the break check cannot fire:
    these tests isolate the episode logic.
    """''', 1)

old_test = tt[tt.index("def test_no_lookahead_levels_match_prefix_runs():"):tt.index("def test_no_lookahead_signals_match_prefix_runs():")]
new_test = '''def test_no_lookahead_levels_match_prefix_runs():
    """A longer series must not change what the scan already knew at candle t.

    The comparison is between the scan stopped at t on the full series and a scan of
    the truncated series, so the level counts are final in both runs. If any part of
    the scan read a later candle, these diverge.
    """
    candles = make_candles(random_walk(300, seed=17))
    foundation = compute_foundation(candles)
    for t in range(60, len(candles), 7):
        stopped_levels, _ = discover_levels(
            candles, foundation, params, collect_signals=False, stop_index=t)
        prefix_candles = candles[: t + 1]
        prefix_levels, _ = discover_levels(
            prefix_candles, compute_foundation(prefix_candles), params,
            collect_signals=False)
        expected = sorted(
            (lv.kind.value, round(lv.lower, 6), round(lv.upper, 6), lv.bounces,
             lv.penetrations, lv.expired_count, lv.state, lv.broken_index)
            for lv in stopped_levels
        )
        actual = sorted(
            (lv.kind.value, round(lv.lower, 6), round(lv.upper, 6), lv.bounces,
             lv.penetrations, lv.expired_count, lv.state, lv.broken_index)
            for lv in prefix_levels
        )
        assert actual == expected, f"levels differ at t={t}"


'''
tt = tt.replace(old_test, new_test, 1)
tp.write_text(tt, encoding="utf-8", newline="\n")
print("patched")