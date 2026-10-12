# Trading Analysis Platform

Market analysis platform: live and historical candles across timeframes, strategy
modules, and one best trade setup (or "no setup"). Analysis only -- version 1 places
no orders.

Mathematical source of truth: `Trading Strategy Mathematical Specification v2`
(Josh, Oct 2026). Only support and resistance has a published formal definition
(Chung and Bellotti, 2021). Every other module is an engineering convention and is
labelled as such in the code and in `DECISIONS.md`.

## Layout

```
apps/web/           React front end (Vite + TradingView Lightweight Charts)
services/api/       FastAPI endpoints
engine/data/        data adapters, candle store, integrity checks
engine/foundation/  ATR, confirmed pivots, structure state (Phase 1)
engine/modules/     one file per strategy module, versioned (Phase 2+)
engine/scoring/     candidate ranking, model, calibration, symbol gate (Phase 7)
engine/backtest/    event driven simulator (Phase 6)
config/             versioned parameter files
tests/              unit and integration tests
```

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # fill in keys, see below
pytest                      # 27 tests, no network needed
```

## Running

```powershell
# 1. load history into the store (real provider data, nothing simulated)
.venv\Scripts\python.exe scripts\seed_store.py --symbols BTC/USD:BTC,R_75 --limit 1000

# 2. API
.venv\Scripts\python.exe -m uvicorn services.api.main:app --port 8000

# 3. web app
cd apps\web; npm install; npm run dev     # http://localhost:5173
```

Chart views are linkable: `http://localhost:5173/?symbol=BTC%2FUSD%3ABTC&timeframe=H4`.

`scripts/smoke_data.py` prints live evidence per provider without writing to the
store unless you pass `--save`.

## Keys and accounts

| Provider | What is needed | Where |
| --- | --- | --- |
| FXCM (forex, gold) | **Blocked.** The old REST API hosts no longer resolve. Current FXCM API is FCLite and wants a trading account login, not an API key. See `DECISIONS.md` D-006. | https://demo.fxcm.app (free demo) |
| Deriv (synthetics) | Optional for reading data: the public test app id `1089` works. Register a real one before any public release. | https://developers.deriv.com |
| Crypto (CCXT) | Nothing for market data. No key is stored. | n/a |

Secrets live in `.env` only, which is gitignored.

## Status

Phase 0: crypto perpetuals (Deribit) and Deriv synthetic indices are loaded and
charting. Forex and gold are blocked on the FXCM transport change. See
`CHANGELOG.md` for what is done and `DECISIONS.md` for every recorded choice,
interpretation and open question.