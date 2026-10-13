# Changelog

Phases 0 to 8 are built. Phase 9 (the MT5 client) is not started. Every result below
comes from real provider data or from hand-built fixtures used as controls; nothing is
simulated and presented as a market result.

## Phase 10 - TradingView-style workspace (2026-10-13)

### Added
- Watchlist sidebar: every stored instrument (433 today) grouped by market class - forex,
  gold, commodities, indices, crypto perpetuals, crypto futures, crypto spot, synthetic
  indices - with a filter box, favourites, and a dot showing which symbols already have
  candles stored.
- Chart: volume pane and an ATR(14) pane drawn from the engine's own Wilder ATR, 20-second
  live polling with a paused/live toggle, per-category overlay toggles (levels, zones,
  pools, FVGs, order blocks, MSS and structure events, MSNR, CRT) each showing how many
  were found, full-screen mode, and zoom that survives a live update (only a symbol or
  timeframe change re-fits the chart).
- Timeframes M1, M5, M15, M30, H1, H4, D1 and W1.
- `GET /candles?refresh=true` and `GET /overlays?refresh=true`: the server polls the venue
  at most once per symbol and timeframe every 20 seconds, answers from the store inside
  that window and says so (`refresh_skipped`), so an open page cannot hammer a provider.
- Weekly candles for venues that do not publish them, resampled from daily candles with
  the session-market rule (five members for a trading week, seven for a 24/7 market;
  holiday weeks are dropped, not filled in).
- The site remembers the symbol, timeframe, live state, overlay toggles, favourites and
  volume/ATR choices in the browser, and keeps `?symbol=&timeframe=` linkable.

### Verified with real data (2026-10-13)
- GBPUSD W1: 49 weekly candles (Oct 2025 to Oct 2026) via `deriv+resample`; XAUUSD W1: 48.
- BTC/USD:BTC W1: 17 candles, native weekly from Deribit.
- GBPUSD M15: 188 closed candles from Deriv; ATR feed 587 points for 600 H1 candles.
- Refresh polling: first call refreshed from the provider, the second inside 20 seconds was
  throttled and answered from the store, as designed.

### Tests
`pytest`: **161 passed** (157 + 4 new: refresh and throttle, weekly resample for a 24/7
market, weekly resample for a five-day market, the ATR feed matching the engine).

## Phase 8 - API and website (2026-10-13)

### Added
- `GET /overlays/{symbol}`: detected levels, zones, pools, grabs, fair value gaps, order
  blocks, structure events, MSS, MSNR levels and CRT setups as chart primitives, each with
  the candle time it became known at, and a `counts` block so the client can say what was
  found against what is drawn.
- `GET /analysis/{symbol}`: the single-setup endpoint. Closed candles only, foundation
  once, every module independently, the symbol gate, the calibrated model on a time-ordered
  split, then the show rule. Returns one setup with entry, stop, target, reasons,
  invalidation, evidence and similar past cases, or `no_setup` with the reason. Cached for
  10 minutes per symbol, timeframe, last closed candle and parameter hash.
- `GET /gate/{symbol}`: the gate on its own, for showing what passed and what did not.
- Website: overlay drawing on the Lightweight Charts candle chart (price lines for levels,
  zones, FVGs, order blocks, pools and MSNR levels; markers for MSS, structure events,
  grabs and CRT), a setup card, an explicit no-setup state, the gate table with each
  module's hit rate, interval and baseline, a symbol search box, timeframes M15/H1/H4/D1,
  mobile layout, and the TradingView Lightweight Charts attribution footer.
- Tests: `tests/test_api_analysis.py`, 7 tests, including an unfinished-candle leak test, a
  no-look-ahead test on the card, a "the phrase high probability never appears" test, a
  thin-history test and a slashed-id routing test (crypto ids contain `/`).

### Fixed
- Scoring was O(candidates x history) per ranking pass: the similarity space was rebuilt
  for every candidate. A live analysis of 694 candles took about 265 seconds. The space is
  now measured once per pass; the same analysis takes about 10 seconds.
- Symbol ids containing a slash (BTC/USD:BTC) could not be routed; those routes now take a
  path parameter.
- A symbol with almost no stored candles failed deep inside the split; it now answers 409
  with a plain message ("only 1 closed H1 candles stored for XAUUSD; the analysis needs at
  least 200").

### Verified with real stored data (2026-10-13)
- GBPUSD H1 (694 closed candles), XAUUSD H1, R_75 H1, BTC/USD:BTC H1, ETH/USD:ETH H1.
- Every symbol returned **no setup**, with the gate naming the reason. Examples:
  GBPUSD H1 - "no module beats its own baseline out of sample"; R_75 H1 - "levels show no
  information against 0.5; no module beats its own baseline"; BTC H1 - "variance ratio
  cannot reject a random walk; no module beats its own baseline".
- GBPUSD H1 module table from that run: liquidity 636 resolved, 0.0% hit rate;
  CRT 81 resolved, 51.9%; support and resistance 6 resolved, 50.0%; MSNR 6 resolved,
  83.3%; SMC, ICT, SLP and liquidity_retrace produced no resolved trades on that sample.
- Cost note on every result so far: "typical spread unknown for this instrument: spread
  cost is not modelled" (Deriv metadata carries no typical spread).

### Tests
`pytest`: **157 passed**.

## Phase 7 - Scoring and the gate (2026-10-13)

### Added
- `engine/scoring/`: 28-feature vector per candidate (module flags, level strength, zone
  score, pool size, sweep depth, ATR distance to target, R, session flag, higher-timeframe
  agreement, and the rest), logistic model (IRLS + L2), calibration verdict, symbol gate.
- The gate: Lo-MacKinlay variance ratio on closes, pooled support/resistance bounce
  probability against 0.5, and each module's out-of-sample hit rate against its own 1/(1+R)
  baseline with the Wilson interval. Passing requires all three, and the gate's reasons are
  returned, not just a boolean.
- The show rule: positive expected value, at least 100 similar past cases, and the lower
  bound of the Wilson interval above the trade's own baseline; otherwise "no setup".
- `python -m engine.scoring.cli --symbol ...` prints the gate, the model and the decision.

### Real gate results
- R_75 H1: variance ratio q=4 p=0.0008 (mean-reverting), bounce probability 0.492 with
  interval [0.420, 0.564] (includes 0.5, so not informative), no module beats baseline.
  Gate failed: no setup. This is the expected outcome for an RNG-generated index and it is
  reported as such, not tuned away.
- GBPUSD H1: variance ratio rejects a random walk and the bounce estimate is informative
  (0.596, interval [0.541, 0.648]), but no module beats its own baseline out of sample, so
  the gate still fails and the answer is no setup.
- BTC H1: the variance ratio cannot reject a random walk; no setup.

## Phase 6 - Backtester and the module-alone study (2026-10-13)

### Added
- Event-driven simulator: a signal is live from `known_index + 1`, limits fill only on a
  later candle's range, entry and outcome expiries, spread/commission/slippage/funding in R.
- Outcome labels (target before stop, expiry rule, the stop wins an ambiguous candle),
  metrics (win rate with Wilson interval, profit factor, expectancy in R, drawdown, trade
  count, average time in trade), time-ordered train/validation/test splits and walk-forward.
- `engine/backtest/study.py`: every module alone against two baselines - p0 = 1/(1+R)
  averaged over its own setups, and a random-anchor control with the same entries and exits
  at random candles.
- Study CLI: `python -m engine.backtest.cli --symbol ... --timeframe ... --bars ...`.

### Real study results (all negative, reported as they are)
- GBPUSD H1, GBPUSD H4, R_75 H1 and BTC H1: every module's Wilson lower bound falls below
  its own p0, or the sample is too small for a verdict.
- The two standalone liquidity readings are the clearest failure: about 500-600 resolved
  trades, **0.0% hit rate**, median reward about 4.5R, and about 40% of them stopped within
  three candles. Their random-anchor controls land near p0 (0.09-0.20), which says the
  simulator is not rigged: the entry convention itself (buying the reclaim close, or
  retesting the swept level) is simply a loser on these samples.
- CRT lands at roughly p0 plus noise. Support/resistance and MSNR have samples too small
  for a verdict at these histories.
- Hand-verification: liquidity trades were checked candle by candle; entries, stops and
  targets are as the convention defines them.

## Phase 5 - SLP (2026-10-12)

- Structure bias from the SMC state machine on the higher timeframe, with the protected
  level ending the bias on a close beyond it by 0.05 x ATR.
- Liquidity event: a grab of an active pool in the direction of the bias, in discount for
  longs and premium for shorts, followed by a lower-timeframe MSS within r = 10 candles.
- POI collection from order blocks, FVGs, supply and demand zones, support/resistance
  intervals and MSNR zones, with the four qualification tests and the overlap score.
- Entry at the proximal edge, stop beyond the farther of the distal edge and the swept
  extreme, target the nearest active opposing pool, enforced event order and expiry.

## Phase 4 - SMC, ICT, CRT and MSNR (2026-10-12)

- SMC: BOS, CHoCH, displacement, MSS, FVG with the three mitigation variants, order blocks,
  and the entry model v1 with event order and expiry.
- ICT: dealing range and equilibrium, OTE, session filters with timezone and daylight
  saving handled, Judas swing, power of three, Silver Bullet, and the ICT entry model.
- CRT: reference candle, sweep and reclaim, targets at the reference mid and far edge.
- MSNR: A and V levels from closes, zones, gap levels, freshness as a score, and setup v1.

## Phase 3 - Liquidity and supply and demand (2026-10-12)

- Pools (equal highs and lows clustered within 0.1 x ATR), the liquidity grab with the one
  candle and r = 3 candle variants, external and internal liquidity, and target selection
  from active opposing pools.
- Supply and demand zones: base, leg in, leg out, proximal and distal lines, freshness and
  a score built from departure, base quality, freshness and range. The zone score is
  labelled a score, never a probability.

## Phase 2 - Support and resistance (2026-10-12)

- Chung and Bellotti definitions: discovery from confirmed swing extremes, the bounce
  counting rule, and the Bayesian strength (n+1)/(N+2) with its variance.
- Acceptance test: pivot-anchored levels give 0.5473 pooled over 11,988 episodes against a
  random-anchor control of 0.5204 over 4,045 episodes, so the module reproduces roughly 0.5
  on random walks with the selection effect documented in DECISIONS.md D-008.

## Phase 1 - Foundation (2026-10-12)

- Wilder ATR with n = 14; confirmed pivots with the spec's tie rule and a `known_index` of
  formation plus k; the structure state machine (HH, HL, LH, LL, bull, bear, none) with a
  protected level, every event carrying formation_time and known_time.
- Look-ahead tests: detectors are asserted to produce identical output when the candles
  after the evaluation index are removed, and `ModuleSignal` refuses known_time earlier than
  formation_time.

## Phase 0 - Skeleton (2026-10-12)

### Added
- Repo scaffold: `apps/web`, `services/api`, `engine/{data,foundation,modules,scoring,backtest}`, `config`, `tests`, `scripts`.
- Versioned parameter file `config/params.v1.yaml` with spec provenance and `[GAP]` markers; SHA-256 of the file exposed by `GET /health`.
- Domain models with UTC enforcement, closed-candle flag, and the shared module-signal record with no-look-ahead validation.
- Data adapter interface plus adapters: FXCM (built, retired transport), CCXT (crypto perps/futures), Deriv (forex, gold and synthetic indices over WebSocket), and an offline fixture adapter.
- SQLite candle store with upserts, closed-only reads, instrument and symbol tables.
- Integrity checks: unfinished-candle drop, market-aware gap classification, staleness.
- FastAPI service: `/health`, `/symbols`, `/instrument/{id}`, `/candles`, `/timeframes`.
- Web app (React + TypeScript + Vite) with a TradingView Lightweight Charts candle chart.
- Scripts: `scripts/smoke_data.py` (live provider evidence) and `scripts/seed_store.py`.

### Verified with real data (not fixtures)
- BTC/USD:BTC perpetual (Deribit): D1, H4 (resampled), H1, M15 in the store.
- R_75 (Deriv): D1, H4, H1, M15 in the store.
- GBPUSD and XAUUSD from the Deriv feed after the FXCM transport was retired (D-006).

### Known blockers at the time
- Forex and gold: FXCM's old REST hosts no longer resolve; the current FXCM API is FCLite
  with a different auth flow and no official Python client. The ForexConnect SDK Josh
  linked has no Python 3.11 wheel (D-006 update). Deriv covers forex and gold meanwhile.
- Crypto venues other than Deribit are unreachable from this machine's DNS, so their
  adapters are unverified here.
