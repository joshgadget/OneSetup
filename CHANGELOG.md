# Changelog

## [Unreleased] - Phase 0 (2026-10-12)

Skeleton: repo, config, data adapters, candle store, instrument metadata, basic chart.
Status: **complete except forex and gold**, which are blocked on the FXCM transport
change (D-006). Crypto and synthetic-index data are live and verified end to end.

### Added

- Repo scaffold: `apps/web`, `services/api`, `engine/{data,foundation,modules,scoring,backtest}`, `config`, `tests`, `scripts`.
- Versioned parameter file `config/params.v1.yaml` with spec provenance and `[GAP]` markers; SHA-256 of the file exposed by `GET /health`.
- Domain models with UTC enforcement, closed-candle flag, and the shared module-signal record with no-look-ahead validation.
- Data adapter interface plus four adapters: FXCM (forex/gold), CCXT (crypto perps/futures), Deriv (synthetic indices over WebSocket), and an offline fixture adapter.
- FXCM adapter written against the official Socket REST API user guide: socket.io handshake, bearer header, instruments, offers metadata, candles (Open/Close/High/Low per side), duplicate collapse, unfinished-candle drop.
- CCXT adapter with H1 to H4 resampling for venues that do not serve a native 4-hour candle (Deribit rejects `4h`).
- Deriv adapter on the current public WebSocket endpoint, `ticks_history` with `style=candles`.
- SQLite candle store with upserts, closed-only reads, instrument and symbol tables.
- Symbol registry: GBPUSD, EURUSD, USDJPY, XAUUSD, BTC/USD:BTC, ETH/USD:ETH, R_75, stpRNG, BTC/USDT:USDT, ETH/USDT:USDT.
- Integrity checks: unfinished-candle drop, market-aware gap classification, staleness, series validation.
- FastAPI service: `/health`, `/symbols`, `/instrument/{id}`, `/candles`, `/timeframes`, with CORS for the dev server.
- Web app (React + TypeScript + Vite) with a TradingView Lightweight Charts candlestick chart, symbol/timeframe controls, stale-feed and gap warnings, attribution footer, and `?symbol=&timeframe=` URL parameters.
- Scripts: `scripts/smoke_data.py` (live provider evidence, nothing simulated) and `scripts/seed_store.py`.

### Verified with real data (not fixtures)

- BTC/USD:BTC perpetual (Deribit) - D1, H4 (resampled), H1, M15 in the store; funding-rate history readable.
- R_75 (Deriv) - D1, H4, H1, M15 in the store.
- Charts screenshot from the running site for both, plus the FXCM-blocked GBPUSD state.

### Tests

`pytest`: **27 passed**. Config, models, store, integrity, FXCM candle parser (using
FXCM's own published sample rows), Deriv parser, API endpoints.

### Known blockers

- **Forex and gold.** FXCM's REST hosts no longer resolve; the current FXCM API is
  FCLite with a different auth flow and no official Python client. Decision needed,
  see `DECISIONS.md` D-006.
- Crypto venues other than Deribit are unreachable from this machine's DNS, so their
  adapters are unverified here.