import asyncio
from engine.config import get_settings
from engine.data.deriv import DerivAdapter
from engine.models import Timeframe

async def main():
    s = get_settings()
    a = DerivAdapter(app_id=s.deriv_app_id or "1089")
    rows = (await a._call({"active_symbols": "brief"})).get("active_symbols") or []
    for m in ("forex", "commodities", "cryptocurrency", "indices"):
        syms = [r["underlying_symbol"] for r in rows if r.get("market") == m]
        print(m, "->", syms[:8], "..." if len(syms) > 8 else "")
    print()
    for sym in ["frxGBPUSD", "frxXAUUSD", "cryBTCUSD", "OTC_NDX"]:
        try:
            cs = await a.get_candles(sym, Timeframe.H1, limit=5)
            print(f"{sym:12} candles={len(cs)} last={cs[-1].close if cs else None} at {cs[-1].open_time if cs else None}")
        except Exception as exc:
            print(f"{sym:12} FAILED {type(exc).__name__}: {exc}")

asyncio.run(main())