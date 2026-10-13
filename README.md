# Trading Analysis Platform

Market analysis platform: live and historical candles across timeframes, a set of strategy
modules, and one best trade setup with entry, stop, target, reasons and invalidation - or
"no setup". Analysis only. Version 1 places no orders and has no execution path.

Mathematical source of truth: `Trading Strategy Mathematical Specification v2` (Josh, Oct
2026). Only support and resistance has a published formal definition (Chung and Bellotti,
2021). Every other module is a practitioner convention, labelled as such in the code and in
`DECISIONS.md`. Nothing in this repository claims any module is proven.

Two rules the code enforces rather than promises:

- no look-ahead: every detected event stores `formation_time` and `known_time`, unfinished
  candles never leave the API, and a signal is only tradeable from `known_time` onward;
- no fake probabilities: a number is called a probability only when it comes from a model
  fitted on past outcomes and calibrated on a later period the model never saw. Otherwise
  the interface calls it a score, and "no setup" is a normal answer.

## Layout

```
apps/web/           React front end (Vite, TypeScript, TradingView Lightweight Charts)
services/api/       FastAPI endpoints and the analysis service
engine/data/        data adapters, candle store, integrity checks
engine/foundation/  Wilder ATR, confirmed pivots, structure state
engine/modules/     one file per strategy module, versioned
engine/scoring/     features, logistic model, calibration, the symbol gate, the show rule
engine/backtest/    event-driven simulator, outcome labels, metrics, splits, module study
config/             versioned parameter files (params.v1.yaml)
tests/              unit, integration and backtest tests
scripts/            data smoke test and store seeding
```

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # optional for now, see Keys below
pytest                      # 157 tests, no network needed
```

## Running

```powershell
# 1. load history into the store (real provider data, nothing simulated)
.venv\Scripts\python.exe scripts\seed_store.py --symbols GBPUSD,BTC/USD:BTC,R_75 --limit 1000

# 2. API
.venv\Scripts\python.exe -m uvicorn services.api.main:app --port 8000

# 3. web app
cd apps\web; npm install; npm run dev     # http://localhost:5173
```

Endpoints that matter:

| Endpoint | What it returns |
| --- | --- |
| `GET /symbols?q=&with_data=` | the catalogue: seed registry plus everything discovery stored |
| `GET /candles?symbol=&timeframe=&limit=` | closed candles, gap and staleness flags |
| `GET /instrument/{id}` | tick size, contract size, minimum stop, spread, hours |
| `GET /overlays/{symbol}?timeframe=` | levels, zones, pools, grabs, FVGs, order blocks, structure events, MSS, MSNR, CRT, each with the time it became known |
| `GET /analysis/{symbol}?timeframe=` | one setup card or `no_setup` with the reason, plus the gate and the model |
| `GET /gate/{symbol}?timeframe=` | the symbol gate alone |

Crypto ids contain a slash (`BTC/USD:BTC`); the routes accept them as one path segment, and
the web app encodes them.

## Command line

```powershell
# every module alone against its baseline, with the random-anchor control
.venv\Scripts\python.exe -m engine.backtest.cli --symbol GBPUSD --timeframe H1 --bars 1200

# the symbol gate, the model and the show decision, printed
.venv\Scripts\python.exe -m engine.scoring.cli --symbol GBPUSD --timeframe H1
```

## Data and keys

| Provider | Covers | What is needed | Status here |
| --- | --- | --- | --- |
| Deriv | forex, gold, synthetic indices (Volatility and Step) | a real `app_id` from https://developers.deriv.com before any public release; the public test id is used meanwhile | working, real data |
| Deribit over CCXT | crypto perpetuals and futures | nothing for market data | working, real data |
| FXCM | forex, gold | see `DECISIONS.md` D-006 and its update. The ForexConnect SDK has no Python 3.11 wheel; FCLite needs a trading account login and a 1-minute token refresh | not connected; forex and gold come from Deriv meanwhile |
| Other crypto venues | crypto | nothing for market data, but their DNS is unreachable from this machine | adapters written, unverified here |

Secrets live in `.env` only, which is gitignored. No key is required to run the tests or to
read crypto and Deriv market data.

Nothing here redistributes provider data: candles are stored locally for analysis on the
machine that fetched them. Deriv's terms, FXCM's terms and the exchange terms must be read
before any redistribution or public hosting of provider data.

## Status

Phases 0-8 are built: skeleton, foundation, support and resistance, liquidity and supply and
demand, SMC/ICT/CRT/MSNR, SLP, the backtester, scoring with the symbol gate, and the API and
website. Phase 9 (the MT5 client) is not started.

Every module fails the gate on every symbol tested so far, including the synthetic index
where that was expected, so the platform currently answers "no setup" almost everywhere.
That is the honest state of the evidence, not a bug list: see `CHANGELOG.md` for the numbers
and `DECISIONS.md` for every reading, interpretation and open question.
