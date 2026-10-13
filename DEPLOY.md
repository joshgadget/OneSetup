# Deployment

Two pieces, two homes:

| Piece | Where | Why |
| --- | --- | --- |
| Website (`apps/web`) | **Vercel** | static Vite build, exactly what Vercel is for |
| Analysis API (`services/api`) | **Render** (or Railway / Fly / any Docker host / a VPS) | long-running Python, a persistent candle store, 15-30s analyses |

**Why the API is not on Vercel serverless:** a serverless function has no persistent disk,
so the SQLite candle store would reset on every deploy and every cold start; the Hobby plan
caps a request at 60 seconds while a fresh analysis takes 15-30; and Vercel Hobby is for
non-commercial use. The site itself is a perfect Vercel fit. If you want everything on
Vercel anyway, the API would have to become stateless (fetch candles per request, no store,
analysis under 60s) - tell me and I will write that variant.

## 1. API first (Render)

1. render.com -> **New** -> **Blueprint** -> pick `joshgadget/OneSetup`. `render.yaml` is
   read automatically (Docker, health check `/health`, a 1 GB disk at `/data`).
2. Set the environment variables it asks for:
   - `DERIV_APP_ID` - your Deriv app id
   - `CMC_API_KEY` - optional, only needed for CMC features
   - `ALLOWED_ORIGINS` - leave empty for now, filled in step 3
3. Wait for the deploy, then check `https://<your-api>.onrender.com/health` - it should
   answer `status: ok` with the params hash.
4. Optional but recommended: load history once from the Render shell so the first click is
   fast instead of waiting on providers:
   ```bash
   python scripts/seed_store.py --symbols GBPUSD,XAUUSD,BTC/USD:BTC,R_75 --limit 1000
   ```
   Nothing breaks without this: every symbol fetches on demand and is stored.

Any other Docker host works the same way:

```bash
docker build -t onesetup-api .
docker run -p 8000:8000 -v onesetup-data:/data --env-file .env onesetup-api
```

## 2. Website (Vercel)

1. vercel.com -> **Add New** -> **Project** -> import `joshgadget/OneSetup`.
2. **Root Directory: `apps/web`** - the repo root is not the site; this is the one setting
   that matters.
3. Framework preset: Vite (comes from `apps/web/vercel.json`).
4. Environment variable: `VITE_API_BASE` = `https://<your-api>.onrender.com`
   (no trailing slash). Vite bakes this in at **build** time, so set it before deploying,
   and redeploy after changing it.
5. Deploy, then copy the site URL, for example `https://onesetup.vercel.app`.

## 3. Close the loop (CORS)

Back on Render -> Environment -> `ALLOWED_ORIGINS` =
`https://onesetup.vercel.app,https://onesetup-git-main-<you>.vercel.app`

Save; the service redeploys. Without this the browser blocks the API calls and the site
shows a fetch error - the API itself is fine.

## 4. Secrets

| Where | What |
| --- | --- |
| `.env` locally (gitignored) | every key, for development |
| Vercel | only `VITE_API_BASE` - it is public and shipped inside the JS bundle |
| Render | `DERIV_APP_ID`, `CMC_API_KEY`, `ALLOWED_ORIGINS` |

Nothing secret is ever committed; a test (`tests/test_tracked_files.py`) fails the build if
a source file is untracked, and `.env` is gitignored.

## 5. What to expect on cheap tiers (honest list)

- Render free plan: no persistent disk (the candle store resets on deploy), the service
  sleeps when idle and the first request after that is slow. The starter plan with the
  1 GB disk is what `render.yaml` asks for.
- A fresh analysis is 15-30 seconds per symbol and timeframe, then cached for 10 minutes in
  that process. Run one worker: SQLite is happiest that way and the cache is per process.
- Live mode polls every 20 seconds per open tab; the API throttles provider calls to one per
  symbol and timeframe per 20 seconds, so a forgotten tab cannot hammer Deriv or Deribit.
- Vercel Hobby is non-commercial; a business deployment needs a paid plan on both sides.
- Data terms: check Deriv's and the exchange's terms before making this public or
  redistributing their data. This repo stores candles locally for analysis only.

## 6. Smoke test after deploying

```bash
curl https://<your-api>.onrender.com/health
curl "https://<your-api>.onrender.com/candles?symbol=GBPUSD&timeframe=H1&limit=5"
curl "https://<your-api>.onrender.com/gate/GBPUSD?timeframe=H1"
```
Then open the Vercel URL: the chart should draw candles with levels, and the analysis panel
should show "analysing..." and then either a setup card or a reasoned "no setup".
