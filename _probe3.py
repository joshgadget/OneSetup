import ccxt
ex = ccxt.deribit({"enableRateLimit": True, "timeout": 30000})
mk = ex.load_markets()
swaps = sorted(s for s, m in mk.items() if m.get("swap"))
futs = sorted(s for s, m in mk.items() if m.get("future"))
print("PERPETUALS:", len(swaps))
for s in swaps: print("   ", s)
print("FUTURES (dated):", len(futs), "first 12:", futs[:12])