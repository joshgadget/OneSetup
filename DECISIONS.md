# Decisions

Every entry is either a recorded choice with its evidence, or **[OPEN]** - something
we could not settle from the specification and are reporting to Josh. Parameter
defaults live in `config/params.v1.yaml`; entries marked `[GAP]` there mirror the
interpretations below.

## D-001 Providers (Phase 0, 2026-10-12)

- **FX + gold: FXCM** (Josh's choice). The adapter is written against FXCM's own
  *Socket REST API User Guide* v2.1.1 (https://fxcm-rest.readthedocs.io/en/latest/socketrestapispecs.html):
  socket.io handshake, `Authorization: Bearer <socket_id><access_token>`,
  `GET /trading/get_instruments/`, `GET /trading/get_model/?models=Offer`,
  `POST /subscribe?pairs=SYMBOL`, `GET /candles/{offer_id}/{period_id}?num=N`.
  **This transport is retired - see D-006. It cannot serve data today.**
- **Crypto: CCXT public endpoints**, venue configurable (`CRYPTO_EXCHANGE`).
  Default changed to **deribit** (see D-006): bybit, binance, okx, kucoin, bitget,
  mexc, htx and gateio do not resolve from this machine's DNS, so their adapters
  cannot be verified here. No key is needed or stored for market data; v1 never
  trades. A perpetual venue that is NOT Deribit needs a different network path.
- **Synthetic indices: Deriv WebSocket API.** Live endpoint verified 2026-10-12:
  `wss://api.derivws.com/trading/v1/options/ws/public` (`ticks_history`,
  `style=candles`, `granularity` in seconds). `ws.derivws.com` and
  `ws.binaryws.com` answer Cloudflare 520 here, so the `/trading/v1/.../public`
  path is the one in use. `DERIV_APP_ID` optional for read-only data; the public
  test id `1089` works. Deriv wants an app id before any public release. Deriv generates these instruments
  with a cryptographically secure RNG; the gate is expected to refuse most modules
  here and "no setup" is the honest output.
- **Licence and rate-limit notes.** FXCM's published data samples state personal use
  under their EULA and redistribution is not granted; we do not redistribute, we
  fetch per user session. FXCM's REST spec does not publish numeric rate limits, so
  the adapter throttles to a configurable 0.25 s minimum between requests and will
  confirm limits with FXCM. Exchange data (CCXT) and Deriv data are subject to each
  venue's terms; Deriv's terms are being read before any public deployment. **[OPEN]**
- **Chart: TradingView Lightweight Charts** (Apache-2.0). The attribution notice is
  rendered in the site footer. **[OPEN]** confirm the exact required attribution
  wording from the official repository before public launch.

## D-002 FXCM candle arrays

Candles arrive as `[ts, BidOpen, BidClose, BidHigh, BidLow, AskOpen, AskClose,
AskHigh, AskLow, TickQty]` - **Open, Close, High, Low per side**, not OHLC. The
adapter stores the configured side (`FXCM_PRICE_SIDE`: `mid|bid|ask`, default
`mid`). Duplicate timestamps appear in FXCM's own documented sample; the adapter
collapses them (last row wins) and the store upserts on
`(symbol, timeframe, open_time)`.

## D-003 No look-ahead and unfinished candles

Adapters drop the current, unfinished candle of every timeframe (including higher
timeframes) when `closed_only=True`, which is the default for every engine path.
The store persists the `is_closed` flag and the API/analysis reads filter on it.
Tests in `tests/test_integrity.py`, `tests/test_fxcm_parser.py` and
`tests/test_deriv_parser.py` enforce the drop.

## D-004 Parameter provenance

`config/params.v1.yaml` carries every convention and starting value from the
specification, each with its source section. The file is hashed (SHA-256) and the
hash is exposed by `GET /health`; Phase 1+ stores the parameter version and hash on
every event so any result can be traced to the exact parameter set.

## D-005 Recorded interpretations for spec gaps

Each item is `[GAP]` in the parameter file. Reported to Josh 2026-10-12.

1. Pivot `k`: spec bounds 3-5. Default `k=3`, per-timeframe H4=4, D1=5. Tie rule
   exactly as the spec formula reads: strict against left neighbours,
   non-strict against right ones.
2. ATR warm-up: Wilder with SMA seed over the first 14 true ranges; no ATR until 14
   closed candles exist; TR spans the previous available candle across weekend and
   daily breaks.
3. Reward to risk `R` is used throughout the spec but never defined:
   `R = |target - entry| / |entry - stop|`, gross; costs live in EV's `c`.
4. S/R discovery needs `i` and `omega`, which the spec never fixes: chosen
   `i = 200` closed candles, `omega = 5`.
5. S/R gamma has two live forms in the spec (trailing mean over `m = 200`, or
   `0.25 x ATR`): default is the trailing mean of `|x_t - x_{t-1}|` over 200 closed
   candles; the ATR form stays available as a variant.
6. Bounce/penetration counting at candle granularity: closes decide enter/exit for
   counting and strength; wicks decide touches for display. Both are stored.
7. S/R gate vs ranking number: the gate uses the empirical bounce rate with a
   Wilson interval; ranking uses the Bayesian (n+1)/(N+2) strength. They are never
   mixed silently.
8. Round-number levels (multiples of `s`): off by default; variant test later.
9. Supply/demand base segmentation: maximal runs of candles with
   `body <= beta x ATR`, length 1-6; longer runs rejected rather than split.
10. Supply/demand confirmation: incremental - the zone confirms at the first close
    that satisfies `D+/- >= alpha_out`, and is frozen from then.
11. Leg-in needs `L_in` prior candles; with fewer, the formation is not classified.
12. Order block zone: full range `[L_j, H_j]` by default; body-only is a separate
    module version.
13. Liquidity pools: single-linkage clustering; the level is the extreme of the
    cluster and is re-derived at each new confirmed contributing swing.
14. Pool state: swept on a grab (1-candle or r-candle reclaim); invalidated on a
    confirmed breakout with no reclaim (`close` beyond `P +/- 0.1 x ATR`); swept
    pools are excluded as targets by default (`swept_pools_as_targets: false`).
15. Liquidity structure range: most recent confirmed swing high and low by
    `known_time`; undefined until both exist.
16. No active pool above/below entry: the module returns no candidate. No target is
    ever fabricated.
17. SLP dealing-range timeframe: the HTF used for bias (default H4), re-frozen on
    new confirmed HTF pivots.
18. SLP POI weights: fitted on training data in Phase 7. Until then Phase 5 tests
    use provisional unit weights and **no client-facing signal is produced**.
19. SLP entry: proximal edge by default (midpoint is a named variant); entry expiry
    20 candles on the entry timeframe; cancel buffer 0.05 x ATR beyond the swept
    extreme.
20. MSS default: standalone detector without the liquidity-grab condition; SLP and
    ICT entry models require it, as written.
21. SMC protected level: most recent confirmed swing low whose formation precedes
    the latest HH and whose `known_time` precedes the HH's `known_time`.
22. FVG mitigation: `midpoint` by default; `touch` and `full` are separate variants;
    "unmitigated" is re-defined per variant.
23. ICT session windows: London 02:00-05:00 and New York 07:00-10:00
    America/New_York, Silver Bullet 10:00-11:00, all configurable. **[OPEN]** Josh's
    educator may quote different windows; his values win when he provides them.
24. Judas swing: window `w = 6` entry-timeframe candles, buffer 0.05 x ATR.
25. Power of three: accumulation = first 4 entry-timeframe candles of the session
    with width below 1 x ATR; the most speculative convention in the spec.
26. OTE legs: the swing low/high pair that brackets the MSS impulse.
27. CRT: H4 reference candle by default (D1 alternate), sweep and reclaim with
    `n = 1` candle allowance, wick sweep with close back inside, expiry 20 reference
    candles, range-width filter 0.5 x ATR.
28. MSNR: `gamma_min = 3` ticks (from instrument metadata), `lambda = 0.7`,
    `k = 1`, follow-up `r = 5`, stop buffer 0.25 x ATR, close-based zones default.
29. Gate: `q` in {2,4,8} (primary 4), Lo-MacKinlay robust z plus block bootstrap;
    evaluated on the most recent 30% of history; needs >= 500 resolved candidates
    and >= 1000 level entries or the result is "insufficient evidence".
30. Cost model for "p0 plus costs": provider typical spread x session multiplier,
    commission from config, slippage from Phase 6.
31. Funding cost: median time-to-resolution for the module/timeframe from the
    training window, converted to intervals; sign by direction.
32. Liquidation guard: stop must be at least `max(0.25 x ATR, 20% of stop distance)`
    beyond `P_liq`; leverage/margin come from a config profile Josh sets.
33. Unresolved backtest trades: exit at market at expiry, count as fractional R in
    metrics; only target-first (1) and stop-first (0) enter the logistic fit.
34. "Similar past cases": standardised Euclidean distance in feature space with a
    configured radius, with module/timeframe/market buckets as fallback.
35. Stale feed / gaps: max age 2 x timeframe; weekend and gold daily-break gaps are
    expected for FX/metal; every unexpected gap blocks signals.
36. Module-to-timeframe plan: one primary timeframe per module plus explicit HTF
    context; each cross-timeframe test is a named module version.
37. Risk rules (daily loss limit, drawdown suspension, duplicate protection, global
    off switch) are API/config guardrails in v1 because nothing places orders.
38. Deriv instrument list: Phase 0 seeds R_75 (Volatility 75) and stpRNG (Step
    Index). **[OPEN]** confirm the exact instrument list Josh wants; "the reef" in
    the spec is unconfirmed.

## D-006 FXCM transport is retired - blocker on forex and gold (Phase 0, 2026-10-12)

Evidence gathered on 2026-10-12 (repeatable; commands in `scripts/` notes below):

1. **DNS.** `api.fxcm.com`, `api-demo.fxcm.com`, `forexconnect.fxcm.com` and
   `fclite.fxcm.com` all return NXDOMAIN, checked with `Resolve-DnsName` locally and
   with DNS-over-HTTPS (so it is not a local resolver fault). `www.fxcm.com` and
   `www.fxcorporate.com/Hosts.jsp` are still up, so the company is alive and only
   the API hosts are gone.
2. **Current offering.** FXCM now publishes **FCLite**
   (https://www.fxcm.com/public-docs/fclite/), whose docs state: *"To use the FCLite
   API, all you need is a valid FXCM trading account"*, with a free demo at
   https://demo.fxcm.app. Its hosts DO resolve from here:
   `endpoints-demo.fxcm.com` (demo) and `endpoints.fxcm.com` (live).
   FCLite has a price-history call (`IPriceHistoryManager.getPrices(instrument,
   timeFrame, fromDate, toDate, quoteCount, callback)`, plus `setIncludeWeekendData`),
   so candles are available.
3. **Auth shape changed.** FCLite authenticates with `loginId` + `password` (or a
   JWT from `https://endpoints-demo.fxcm.com/iam/swagger-ui/index.html`, or OAuth for
   brokers/vendors), POSTed to `/iam/authenticate` with `X-COOKIE-DOMAIN` and
   `X-XSRF-TOKEN` headers. The access token lives **1 minute** and must be refreshed
   on a loop against `/iam/refresh/`. This replaces the long-lived Trading Station
   Web token, so the key Josh offered to send does not exist in the old form.
4. **No official Python client.** FXCM's FCLite samples and libraries are Node
   TypeScript/JavaScript, Java and C#; the older Python wrapper
   (`forexconnect`) is no longer maintained, and the PyPI wheel tops out at
   **cp310** - it will not install on this machine's Python 3.11. So the FCLite
   client must be written by us against the documented protocol.
5. **Access cost, unconfirmed.** Community reports (r/algotrading, 2025-2026) say a
   *live* FXCM account needs roughly a **$5,000 deposit** before API access is
   enabled; a **demo account is free**. We have not verified this with FXCM and
   will not state it as fact. Phase 0/1 only needs a demo account either way.

**Consequence.** GBPUSD and XAUUSD cannot be loaded today. `GET /candles?symbol=GBPUSD`
returns HTTP 502 with the blocker in the message. The adapter keeps its documented
REST implementation so the work is not lost, and `engine/data/fxcm.py` raises an
actionable error rather than pretending to be healthy.

**Options put to Josh (decision needed before forex work continues):**

| Option | Effort | Notes |
| --- | --- | --- |
| FXCM FCLite, free demo account | Medium | Keeps his FXCM choice. We write the client; must handle the 1-minute token refresh. |
| FXCM FCLite, live account | Medium | Same client. Live API access terms/deposit to be confirmed with FXCM first. |
| MT5 bridge (any broker) | Medium | MQL5 EA pulls candles from a terminal Josh already runs and POSTs them in. Broker-agnostic, and Phase 9 needs MQL5 anyway. |
| OANDA v20 REST | Low | Simple bearer token, `api-fxpractice.oanda.com` is up (HTTP 401 without a key), candles for FX and XAU_USD, free practice account. Would replace FXCM. |

Whichever he picks, the adapter interface (`get_candles`, `subscribe`,
`get_instrument_metadata`) does not change; only `engine/data/fxcm.py` or a sibling
module does.

### D-006 update (Phase 8, 2026-10-13): the ForexConnect repository Josh linked

Josh pointed to https://github.com/fxcm/ForexConnectAPI.git and asked us to work from it.
Checked on 2026-10-13:

- That repository is FXCM's **ForexConnect SDK distribution**, not a Python package. The
  repo README says: "Forex Connect supports C++, C#, Java, VB, VBA, Windows, Linux and
  smart phones. And it is free." Its top-level folders are the native SDK plus samples.
- The **Python binding is separate**: the `forexconnect` package on PyPI. Checked
  `pypi.org/pypi/forexconnect/json`: latest is 1.6.43 and the only Windows wheels are
  cp35, cp36 and cp37 (win32 and win_amd64). There is **no cp311 wheel**, so
  `pip install forexconnect` in this Python 3.11 venv fails: "
  ERROR: No matching distribution found for forexconnect" (verified both with
  `pip index versions` and `pip download --only-binary :all: --python-version 311`).
- So the SDK cannot run inside the engine process as it stands.

Options, in the order they now stand (the adapter interface does not change for any):

| Option | Effort | Notes |
| --- | --- | --- |
| ForexConnect bridge on Python 3.7 x64 | Medium | the cp37 win_amd64 wheel exists. A small second process uses the SDK, pulls candles and writes them into the same SQLite store or POSTs them in. Keeps FXCM, adds an interpreter to install. |
| FXCM FCLite REST/WebSocket | Medium | documented, no official Python client, 1-minute access token that must be refreshed on a loop. |
| MT5 bridge (MQL5 EA) | Medium | broker-agnostic, works with whatever terminal Josh runs, and Phase 9 needs MQL5 anyway. |
| OANDA v20 REST practice | Low | simplest read path, but a different vendor; practice host is live at `api-fxpractice.oanda.com`. |

Until one is chosen and credentials exist, **forex and gold are served by the Deriv
adapter** (`frx*` symbols, e.g. `frxGBPUSD`) and stored under the same canonical ids
(GBPUSD, XAUUSD...). That is a real feed, not simulated data, but it is Deriv's own
quotes with Deriv's own spreads, so it is not FXCM's book. Costs from it are unmodelled
because Deriv metadata carries no typical spread (the analysis says so in its notes).

## D-007 Charting (Phase 0, 2026-10-12)

Answered Josh's question "can we use TradingView?".

- **Using now: TradingView Lightweight Charts** (Apache-2.0), the open-source
  library TradingView publishes for exactly this. It is already in
  `apps/web/src/components/PriceChart.tsx` and renders the candlestick chart with
  the TradingView mark and an attribution line in the footer, as Apache-2.0 and
  TradingView's attribution requirement ask. No account, no key, no cost.
- **Not using: the TradingView "Charts Library" / Advanced Charts and the
  TradingView widget.** The Advanced Charts library is a separate, licence-gated
  download (approval by TradingView, attribution and usage terms attached), and the
  embeddable widget cannot show our own detected levels, zones and setup objects
  without paying for the charting-integration path. Both would also mean the chart
  is drawn by someone else's black box, which conflicts with rule 8 (we must be able
  to say exactly where a level came from).
- **[OPEN]** Confirm the exact attribution wording TradingView requires from the
  official repository before any public launch.
- Added `?symbol=...&timeframe=...` URL parameters to the web app so a chart view is
  linkable and screenshottable. `apps/web/src/App.tsx`.

## D-008 Support and resistance: what counts as a bounce (Phase 2, 2026-10-12)

The only module with a published formal definition. Chung and Bellotti (2021), as the spec
transcribes it: for support, x_t in [a, b] implies P(x_{t+d} > b) > P(x_{t+d} < a) for
d = 1..omega; resistance mirrors it; discovery is Support = min X_t +/- gamma and
Resistance = max X_t +/- gamma; strength is the Bayesian E[p] = (n+1)/(N+2) with
Var[p] = (n+1)(N-n+1)/((N+3)(N+2)^2).

**Reading chosen for an episode.** Every close inside the interval opens an episode; the
episode resolves at the first close beyond an edge (near edge = bounce, far edge =
penetration) or expires after omega candles still inside. Episodes still inside are
excluded, as the paper's conditional probability excludes them.

**Two readings were measured and rejected** (both on driftless synthetic random walks,
which is the spec's acceptance test):
- counting near-edge crossings and halving them, as the formula reads literally:
  about 0.92, because a level anchored on a swing is crossed at its near edge far more
  often than at its far edge;
- counting only the first entry from the near side: about 0.59.

**Measured acceptance result** (`tests/test_support_resistance.py`, 7 seeds):

| anchors | bounce probability | episodes |
| --- | --- | --- |
| random (control) | 0.5204 | 4,045 |
| confirmed pivots (as shipped) | 0.5473 | 11,988 |

The 2.7-point gap is a selection effect, not a bug: a band anchored on a confirmed swing
sits where price recently turned, so price inside it leaves through the near edge slightly
more often than half the time even with no order flow at all. This is the noise floor, and
it is why the symbol gate cannot treat "above 0.5" as evidence by itself.

**Our additions, all named parameters:** gamma from a trailing window (m = 200) or
0.25 x ATR; a level from every confirmed swing, with levels within gamma merged into a new
interval while the originals are kept frozen as `merged` so a later merge cannot rewrite
published evidence; age weighting w = exp(-lambda * candles) as a separate ranked estimate;
break confirmation on a close beyond the edge by 0.1 x ATR; optional round-number grid
(off until tested). The signal rule v1 (long after a close back above the support band,
stop at a - 0.25 x ATR, target the lower bound of the nearest resistance band) is a
convention of ours and is labelled as one in the code.

## D-009 SMC readings (Phase 4, 2026-10-12)

SMC has no published formal definition; the spec fixes one reading, and these three points
were still open, so they are ours:

- "known before t" for the latest swing high and low is read **strictly**:
  `known_index <= t - 1`, so the breaking candle can never be its own reference;
- "the swept low" in the entry-model stop rule is the **sweep candle's wick extreme**, the
  deepest point of the sweep, which also sits at or below the pool level;
- "formed in the MSS leg" means the zone is **confirmed at or after the MSS candle**.

Defaults as parameters: BOS buffer 0.05 x ATR; displacement body 1.0 x ATR and close ratio
0.75; standalone MSS does not require a liquidity grab (requiring one is a config flag);
FVG minimum width 0.1 x ATR; FVG mitigation variant default `midpoint`, with `touch` and
`full` kept as separate variants; order-block zone variant default `full_range`, body-only
available. OHLC candles cannot show that institutions placed orders in any candle; these
detectors find patterns with those names and nothing more.

## D-010 CRT: which candle is the reference (Phase 4, 2026-10-12)

The spec fixes the CRT reading and says to name the variant; the open points are ours:

- the reference R is the **latest reference-timeframe candle that had fully closed before
  the signal candle's close** (`interval_end(R) <= moment`), reference timeframe H4 by
  default; with no reference series supplied the trading series is its own reference;
- sweep = wick beyond H_R + eps with the close back inside; the reclaim may be the same
  candle or the next (`sweep_reclaim_candles = 1`); the setup is known at the close of the
  reclaiming candle;
- entry variant `close_entry` (entering on that close). Waiting for a lower-timeframe MSS
  is a separate variant and is not mixed in;
- filters: skip ranges narrower than 0.5 x ATR; stop at the sweeping wick +/- 0.1 x ATR;
  expiry 20 candles (our value; the spec leaves the horizon open).

## D-011 MSNR readings (Phase 4, 2026-10-12)

MSNR is a community method; the spec names the reading and these points were ours:

- k = 1 close each side for an A level (a local peak of the close), so the level is known
  one candle later;
- zone gamma = max(3 ticks, 0.1 x ATR), close-based levels only; body-based zones are a
  separate module and are not mixed in;
- **the gap level price is the midpoint of the gap** (ours; the spec says "gap level"
  without naming the price);
- **the setup v1 target is the near edge of the nearest A-level zone above entry** (ours);
- freshness F = exp(-0.7 N) is reported as a score, never as a probability;
- touch, break and bounce are counted with the same rules as D-008, so each level carries
  the same Bayesian bounce probability.

## D-012 ICT anchors (Phase 4, 2026-10-12)

ICT is a teaching framework, not a published model. The spec fixes the sequence; these
anchors were ours:

- session membership is decided by the **candle's open time** converted into the session
  timezone; the shipped windows are `london 02:00-05:00`, `new_york 07:00-10:00` and
  `silver_bullet 10:00-11:00` in **America/New_York**, and the config marks them "verify
  with educator" **[OPEN: confirm with Josh's educator]**;
- OTE anchors: the latest confirmed swing low before the MSS, and the highest high between
  that low and the MSS candle;
- Judas swing: first w candles of the session, known at the close of the candle that
  reclaims the session open, never earlier;
- power of three in order: accumulation (a range narrower than 1 x ATR), manipulation
  (a Judas swing out of it), distribution (a displacement away from it); all three or
  nothing;
- "a prior sweep" in the Silver Bullet is an **opposite-side grab inside the configured
  window** (ours; the spec does not define the sweep reference).

## D-013 SLP readings (Phase 5, 2026-10-12)

SLP has no published source; the spec says the definitions are its own, and these points
were still open:

- the equilibrium that gates the liquidity event and the POI discount rule comes from the
  **lower-timeframe dealing range**;
- a POI's formation window is `[grab.known_index, MSS candle]`, and its selection (t3)
  happens at the MSS close;
- the distance rule (d <= 3 ATR) is measured **at the selection candle from the nearest
  edge** of the zone;
- a candle that closes beyond the swept extreme is never itself the entry candle; entry
  expiry is 20 candles;
- POI overlap weights are **provisional unit weights** until a fit exists, so the POI score
  is a rank and never a probability;
- MSNR gap levels are not used as POIs in v1.

## D-014 Backtest and study readings (Phase 6, 2026-10-13)

Readings we had to fix to make a no-look-ahead backtest possible:

- **moneyness is R only** in v1: no position size, no contract size, no account currency.
  Win/loss, expectancy and drawdown are all in units of risk;
- when one candle contains both the stop and the target the intrabar order is unknowable,
  so the label **takes the stop**; claiming the target came first would be look-ahead the
  data cannot support;
- **liquidity has no entry rule in the spec** (it defines pools, grabs and target
  selection). Two standalone readings were built and *both are reported*:
  `liquidity` enters at the close of the reclaiming candle, `liquidity_retrace` waits for a
  limit at the swept pool level. Reporting both, and counting them as two variants, is part
  of the protocol so a failed idea cannot hide inside an average;
- supply and demand *does* have an entry rule v1 in the spec and it is implemented here:
  limit at the proximal line on the first return to a fresh zone, stop at the distal line
  -/+ 0.1 x ATR, target the nearest opposing zone's proximal line;
- an order is live from `known_index + 1` and never on the signal candle; a limit fills
  only when a later candle's range touches it; entry expiry 20 candles; unresolved trades
  exit at market after 40 candles;
- costs: spread from provider metadata when it exists; commission and slippage default to
  0 and are named parameters; **costs that cannot be resolved are reported as notes, never
  invented** ("typical spread unknown for this instrument: spread cost is not modelled");
- perpetual funding is subtracted when a funding rate is supplied, 3 intervals per day;
- two baselines: p0 = the mean 1/(1+R) over the module's own setups, and a random-anchor
  control with the same entries, stops, targets and expiry taken at uniform random candles
  with a fixed seed, so a trending period cannot hand a module its edge for free;
- below 30 resolved out-of-sample trades the study reports **no verdict**, never a lucky
  one. Splits are train 0.5 / validation 0.2 / test 0.3 in time order, with a walk-forward
  option.

## D-015 Scoring and the show rule (Phase 7, 2026-10-13)

- model: logistic `sigmoid(w^T x)` fitted by IRLS with L2 (l2_penalty 1.0, at most 50
  iterations); missing features are filled with 0.0 after standardisation and **the count
  of missing values travels with the model**, so the gap is visible;
- **probability or score**: a number is called a probability only when the model was
  fitted on past outcomes *and* the calibration check on the later period it never saw
  passes: expected calibration error <= 0.05 and at least 500 validation cases. Otherwise
  the interface calls it a **score** and the code refuses to rank by it;
- the reliability buckets and the Brier score are reported either way; the test period is
  read once and never used to refit;
- "similar past cases" is measured on a 10-feature subset with an explicit radius:
  `similar_radius` (1.0) x sqrt(d) in per-dimension standard deviations measured on the
  history itself, with a Wilson interval on the hit rate of the matching cases;
- candidates are ranked by **expected value** `EV = p * R - (1 - p) - cost_R`, never by raw
  probability;
- the show rule: gate passed AND model calibrated AND EV > 0 AND at least 100 similar past
  cases AND the Wilson lower bound on those cases clears the trade's own p0. Anything else
  returns "no setup" with the reason, which is a normal and common answer;
- wording rule for every client: "score" unless calibrated, and the phrase "high
  probability" is banned from the interface (a test asserts it never appears);
- performance note (Phase 8): the similarity space is measured once per ranking pass. The
  first version rebuilt the history features for every candidate, which made a live
  analysis take about three minutes on 694 candles; it is now about 1 second.

## D-016 Live workspace: polling, weekly candles and the ATR feed (2026-10-13)

Two rules the TradingView-style workspace needed, recorded because both are readings of
ours:

- **A weekly candle on a session market is complete at five daily members.** Deriv
  publishes no weekly timeframe, so W1 is resampled from D1 when the venue has none. The
  resampler (`engine/data/resample.py`) still refuses to guess: a bucket is aggregated only
  when it has exactly the expected number of members - 5 for forex, gold, commodities and
  indices, 7 for crypto and synthetic indices (which trade every day). A holiday week has
  four members and is therefore **dropped, not filled in**. Venues that publish weekly
  candles natively (Deribit over CCXT) are used as they are. Verified live: GBPUSD 49 weekly
  candles and XAUUSD 48 from `deriv+resample`, BTC 17 natively from Deribit.
- **The browser polls, the server throttles.** The site refreshes the chart every 20
  seconds. The API allows at most **one provider call per symbol and timeframe every 20
  seconds**; inside that window it answers from the store and tells the client the poll was
  throttled (`refresh_skipped`), so a page left open cannot hammer a venue or burn a rate
  limit. The full analysis (the expensive call) stays cached for 10 minutes and is only
  re-run when the user asks.

The ATR pane draws the engine's own Wilder ATR(14) - the same series every detector uses -
served by `/overlays`, never recomputed in the browser. The TradingView-style toggles only
hide or show what the detectors found; nothing in the interface computes a level.
