# The analysis API: FastAPI plus the engine. The website is deployed separately as a
# static build (Vercel), so this image only contains Python.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/trading.sqlite3

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY engine ./engine
COPY services ./services
COPY config ./config
COPY scripts ./scripts

# The candle store lives on a mounted volume so it survives a redeploy. Without a
# volume the API still works: it fetches candles from the providers on demand.
VOLUME ["/data"]
EXPOSE 8000

CMD ["sh", "-c", "uvicorn services.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
