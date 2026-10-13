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

Render runs the Python API - the half that actually does the analysis. The website
cannot work without it, so do this side first.

**Choose the plan first, because it decides which blueprint file you use:**

| | `render.yaml` (default) | `render.paid.yaml` |
| --- | --- | --- |
| Plan | free | starter (roughly $6-7/month, see render.com/pricing) |
| Candle store | inside the instance; lost when the instance is replaced or redeployed | on a 1 GB disk at `/data`, survives |
| Idles | spins down after 15 minutes without traffic | always on |
| CPU | 0.1 | dedicated |

Nothing breaks on free: when the store is empty the API fetches each symbol from the
providers again. The cost is time - the first analysis of a symbol is slower, and the
first click after an idle period waits for the instance to wake up.

1. render.com -> **New** -> **Blueprint** -> pick `joshgadget/OneSetup`. `render.yaml` is
   read automatically (Docker, health check `/health`, free plan).
2. Set the environment variables it asks for:
   - `DERIV_APP_ID` - your Deriv app id
   - `CMC_API_KEY` - optional, only for CMC features
   - `ALLOWED_ORIGINS` - leave empty for now, it is filled in after the site exists
3. Wait for the deploy, then check `https://<your-api>.onrender.com/health` - it should
   answer `status: ok` with the params hash. On a free instance the first request may
   take a minute while it wakes up.
4. Optional: load history once from the Render shell so the first click is fast instead
   of waiting on the providers:
   ```bash
   python scripts/seed_store.py --symbols GBPUSD,XAUUSD,BTC/USD:BTC,R_75 --limit 1000
   ```
   Nothing breaks without this. On the free plan the store is rebuilt from the providers
   after a redeploy, so re-run it if you want the speed back.

To switch to the paid variant, either ask me and I will make `render.paid.yaml` the
default file, or point the Blueprint at that path if the dashboard offers a path field.

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

- Render free plan: no disk (the candle store is rebuilt after the instance is
  replaced), 0.1 CPU, and it spins down after 15 minutes without traffic. An analysis
  that takes 15-30 seconds locally will take minutes there, and the first click after an
  idle period waits for the wake-up. `render.paid.yaml` is the always-on variant with
  the 1 GB disk.
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
