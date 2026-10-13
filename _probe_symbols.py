import asyncio, collections, json
from engine.config import get_settings
from engine.data.deriv import DerivAdapter

async def main():
    s = get_settings()
    a = DerivAdapter(app_id=s.deriv_app_id or "1089")
    msg = await a._call({"active_symbols": "brief"})
    rows = msg.get("active_symbols") or []
    print("TOTAL active symbols:", len(rows))
    print("FIELDS:", sorted(rows[0].keys()))
    by_market = collections.Counter(str(r.get("market")) for r in rows)
    print("BY MARKET:", dict(by_market))
    by_sub = collections.Counter((str(r.get("market")), str(r.get("submarket"))) for r in rows)
    for (m, sm), n in sorted(by_sub.items()):
        print(f"  {m:20} {sm:28} {n}")
    print("\nSAMPLE ROWS:")
    for r in rows[:3]:
        print(json.dumps(r, indent=2)[:900])
    print("\nSYNTHETIC SAMPLE:")
    for r in rows:
        if r.get("market") == "synthetic_index":
            print(json.dumps(r)[:400]); break

asyncio.run(main())