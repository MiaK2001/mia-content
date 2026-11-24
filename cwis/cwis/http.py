"""HTTP-Client mit Rate-Limit pro Host, Timeouts, Wiederholungen und Health-Tracking."""
from __future__ import annotations

import asyncio
import random
import time
from urllib.parse import urlparse

import httpx

from cwis import __version__
from cwis.health import HealthRegistry

# Konservative Standard-Limits (Anfragen pro Sekunde) – deutlich unter den dokumentierten Grenzen.
DEFAULT_RATES = {
    "api.coingecko.com": 0.2,        # Free/Demo: ~5–30/min -> max. 12/min
    "data-api.binance.vision": 5.0,
    "api.binance.com": 5.0,
    "www.okx.com": 4.0,              # öffentliche Endpunkte meist 10–20 Req/2s
    "mempool.space": 2.0,
    "ethereum-rpc.publicnode.com": 4.0,
    "api.trongrid.io": 1.0,          # ohne Schlüssel sehr niedrige Limits
    "bsc-rpc.publicnode.com": 3.0,
    "arbitrum-one-rpc.publicnode.com": 3.0,
    "base-rpc.publicnode.com": 3.0,
    "polygon-bor-rpc.publicnode.com": 3.0,
    "avalanche-c-chain-rpc.publicnode.com": 3.0,
}


class HttpError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


class _Bucket:
    def __init__(self, rate: float):
        self.interval = 1.0 / rate
        self.next_ok = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = time.monotonic()
            if now < self.next_ok:
                await asyncio.sleep(self.next_ok - now)
            self.next_ok = max(now, self.next_ok) + self.interval


class HttpClient:
    def __init__(self, health: HealthRegistry, rates: dict[str, float] | None = None, timeout: float = 15.0):
        self.health = health
        self.rates = {**DEFAULT_RATES, **(rates or {})}
        self._buckets: dict[str, _Bucket] = {}
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": f"CWIS/{__version__} (read-only monitoring)", "Accept": "application/json"},
            follow_redirects=True,
        )
        self._penalty_until: dict[str, float] = {}

    def _bucket(self, host: str) -> _Bucket:
        if host not in self._buckets:
            self._buckets[host] = _Bucket(self.rates.get(host, 2.0))
        return self._buckets[host]

    async def request(self, method: str, url: str, *, source: str, retries: int = 3, **kw):
        host = urlparse(url).hostname or ""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            pen = self._penalty_until.get(host, 0) - time.monotonic()
            if pen > 0:
                await asyncio.sleep(pen)
            await self._bucket(host).wait()
            t0 = time.perf_counter()
            try:
                r = await self._client.request(method, url, **kw)
                latency = (time.perf_counter() - t0) * 1000
                if r.status_code == 429 or r.status_code == 418:
                    retry_after = float(r.headers.get("Retry-After", "30") or 30)
                    self._penalty_until[host] = time.monotonic() + min(retry_after, 300)
                    raise HttpError(f"Rate-Limit ({r.status_code}) bei {host}", r.status_code)
                if r.status_code >= 500:
                    raise HttpError(f"Serverfehler {r.status_code} bei {host}", r.status_code)
                if r.status_code >= 400:
                    # 4xx (außer 429) nicht wiederholen – z. B. 451 Geo-Sperre
                    err = HttpError(f"HTTP {r.status_code} bei {host}: {r.text[:120]}", r.status_code)
                    self.health.error(source, str(err))
                    raise err
                data = r.json()
                self.health.ok(source, latency_ms=round(latency, 1))
                return data
            except HttpError as e:
                last_exc = e
                if e.status and 400 <= e.status < 500 and e.status not in (429, 418):
                    raise
            except (httpx.HTTPError, ValueError) as e:
                last_exc = e
            if attempt < retries:
                await asyncio.sleep(min(2 ** attempt + random.random(), 30))
        self.health.error(source, f"{type(last_exc).__name__}: {last_exc}")
        raise HttpError(str(last_exc))

    async def get(self, url: str, *, source: str, **kw):
        return await self.request("GET", url, source=source, **kw)

    async def post(self, url: str, *, source: str, **kw):
        return await self.request("POST", url, source=source, **kw)

    async def aclose(self) -> None:
        await self._client.aclose()
