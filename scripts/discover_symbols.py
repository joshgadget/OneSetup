"""Ask every provider what it lists and store the catalogue.

    .venv\\Scripts\\python.exe scripts\\discover_symbols.py            # all providers
    .venv\\Scripts\\python.exe scripts\\discover_symbols.py --venue deriv

Reports per provider: how many instruments were found, split by market class, and
the exact error for any provider that failed. Nothing is invented to fill a gap.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.data.discovery import discover_and_store  # noqa: E402
from services.api.deps import get_ccxt, get_deriv, get_store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue", default="all", choices=["all", "deriv", "ccxt"])
    args = parser.parse_args()

    adapters = {"deriv": get_deriv(), "ccxt": get_ccxt()}
    if args.venue != "all":
        adapters = {args.venue: adapters[args.venue]}

    async def run() -> dict:
        try:
            return await discover_and_store(get_store(), adapters)
        finally:
            for adapter in adapters.values():
                await adapter.aclose()

    report = asyncio.run(run())
    print(json.dumps(report, indent=2))

    stored = get_store().list_symbols()
    by_class: dict[str, int] = {}
    for info in stored:
        by_class[info.market_class.value] = by_class.get(info.market_class.value, 0) + 1
    print("\nSymbols in store:", len(stored))
    for name, count in sorted(by_class.items()):
        print(f"  {name:16} {count}")
    return 0 if all(p.get("ok") for p in report["providers"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())