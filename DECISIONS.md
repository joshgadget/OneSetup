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
