"""The CCXT adapter must survive being used from more than one event loop.

A cached ccxt client schedules its rate-limit work on the loop that created it, so a
second loop (a script, a test, a restarted worker) sees "Event loop is closed" unless the
adapter drops the stale client. Found live on 2026-10-13 while analysing BTC then ETH.
"""
from __future__ import annotations

import asyncio

from engine.data.crypto_ccxt import CCXTAdapter


def test_a_client_bound_to_another_loop_is_rebuilt():
    adapter = CCXTAdapter(exchange="deribit")
    sentinel = object()
    adapter._exchange = sentinel
    adapter._loop = None  # no loop can be None, so the cached client is stale

    async def get_client():
        return await adapter._ex()

    client = asyncio.run(get_client())

    assert client is not sentinel, "the stale client was reused across loops"
    assert adapter._loop is not None


def test_the_same_loop_reuses_the_client():
    adapter = CCXTAdapter(exchange="deribit")

    async def twice():
        first = await adapter._ex()
        second = await adapter._ex()
        return first is second

    assert asyncio.run(twice()) is True
