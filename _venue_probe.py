import ccxt, traceback

for venue in ["deribit", "bybit", "okx", "kraken", "krakenfutures", "bitfinex", "coinbase", "gemini"]:
    try:
        ex = getattr(ccxt, venue)({"enableRateLimit": True, "timeout": 20000})
        markets = ex.load_markets()
        swap = [s for s, m in markets.items() if m.get("swap")]
        fut = [s for s, m in markets.items() if m.get("future")]
        ohlcv = None
        try:
            target = None
            for cand in ["BTC/USDT:USDT", "BTC/USD:BTC", "BTC/USD", "BTC/USDT"]:
                if cand in markets:
                    target = cand
                    break
            if target:
                rows = ex.fetch_ohlcv(target, "1h", limit=3)
                ohlcv = f"{target} rows={len(rows)} last={rows[-1][:5]}"
        except Exception as exc:
            ohlcv = f"ohlcv error: {type(exc).__name__}: {exc}"
        print(f"{venue:15} markets={len(markets):6} swaps={len(swap):6} futures={len(fut):5} | {ohlcv}")
    except Exception as exc:
        print(f"{venue:15} FAILED {type(exc).__name__}: {str(exc)[:160]}")