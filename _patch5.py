import pathlib
dp = pathlib.Path(r"C:\Users\Hi\trading\engine\data\deriv.py")
t = dp.read_text(encoding="utf-8")

old = '''class DerivAdapter(DataAdapter):
    name = "deriv"'''
new = '''class DerivTransportError(DataAdapterError):
    """Connection-level failure (reset, timeout, DNS). Worth retrying; an API error is not."""


class DerivAdapter(DataAdapter):
    name = "deriv"'''
assert old in t
t = t.replace(old, new, 1)

old = '''    async def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        req_id = self._next_id()
        payload = {**payload, "req_id": req_id}
        try:
            async with websockets.connect(self.url, open_timeout=self.timeout, close_timeout=5) as ws:
                await ws.send(json.dumps(payload))
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                    msg = json.loads(raw)
                    if msg.get("req_id") != req_id:
                        continue
                    if msg.get("error"):
                        err = msg["error"]
                        raise DataAdapterError(f"Deriv error {err.get('code')}: {err.get('message')}")
                    return msg
        except (OSError, asyncio.TimeoutError) as exc:
            raise DataAdapterError(f"Deriv websocket call failed: {exc}") from exc'''
new = '''    async def _call(self, payload: dict[str, Any], *, attempts: int = 3) -> dict[str, Any]:
        """One request. Transport failures are retried with backoff because this
        network drops websocket connections; API errors are returned immediately."""
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return await self._call_once(payload)
            except DerivTransportError as exc:
                last = exc
                log.warning("Deriv transport error (attempt %d/%d): %s", attempt + 1, attempts, exc)
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.75 * (2 ** attempt))
        assert last is not None
        raise last

    async def _call_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        req_id = self._next_id()
        payload = {**payload, "req_id": req_id}
        try:
            async with websockets.connect(self.url, open_timeout=self.timeout, close_timeout=5) as ws:
                await ws.send(json.dumps(payload))
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                    msg = json.loads(raw)
                    if msg.get("req_id") != req_id:
                        continue
                    if msg.get("error"):
                        err = msg["error"]
                        raise DataAdapterError(f"Deriv error {err.get('code')}: {err.get('message')}")
                    return msg
        except (OSError, asyncio.TimeoutError) as exc:
            raise DerivTransportError(f"Deriv websocket call failed: {exc}") from exc'''
assert old in t
t = t.replace(old, new, 1)

old = '''        except (OSError, asyncio.TimeoutError) as exc:
            raise DataAdapterError(f"Deriv tick stream failed: {exc}") from exc'''
new = '''        except (OSError, asyncio.TimeoutError) as exc:
            raise DerivTransportError(f"Deriv tick stream failed: {exc}") from exc'''
assert old in t
t = t.replace(old, new, 1)
dp.write_text(t, encoding="utf-8", newline="\n")
print("deriv retry added")

sp = pathlib.Path(r"C:\Users\Hi\trading\scripts\discover_symbols.py")
t = sp.read_text(encoding="utf-8")
old = '''    report = asyncio.run(discover_and_store(get_store(), adapters))
    print(json.dumps(report, indent=2))'''
new = '''    async def run() -> dict:
        try:
            return await discover_and_store(get_store(), adapters)
        finally:
            for adapter in adapters.values():
                await adapter.aclose()

    report = asyncio.run(run())
    print(json.dumps(report, indent=2))'''
assert old in t
t = t.replace(old, new, 1)
sp.write_text(t, encoding="utf-8", newline="\n")
print("script closes adapters")