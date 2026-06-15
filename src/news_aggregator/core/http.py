"""httpx async client 封裝：timeout、retry（指數退避）、per-host rate limit。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

import httpx

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RateLimiter:
    """每個 host 之間維持最小請求間隔。"""

    def __init__(self, per_second: float):
        self.min_interval = 1.0 / per_second if per_second and per_second > 0 else 0.0
        self._last: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, host: str) -> None:
        if self.min_interval <= 0:
            return
        async with self._locks[host]:
            wait = self._last[host] + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[host] = time.monotonic()


class HttpClient:
    """共用的 async HTTP client，內建 retry 與 rate limit。"""

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_retries: int = 3,
        rate_limit_per_host: float = 5.0,
        headers: dict[str, str] | None = None,
    ):
        self.max_retries = max(1, max_retries)
        self.limiter = RateLimiter(rate_limit_per_host)
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers or {"User-Agent": "news-aggregator/0.1 (+personal)"},
        )

    @property
    def raw(self) -> httpx.AsyncClient:
        return self._client

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, url, *, headers=None, params=None) -> httpx.Response:
        return await self._request("GET", url, headers=headers, params=params)

    async def post(self, url, *, headers=None, json=None) -> httpx.Response:
        return await self._request("POST", url, headers=headers, json=json)

    async def _request(self, method, url, *, headers=None, params=None, json=None) -> httpx.Response:
        host = httpx.URL(url).host or ""
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            await self.limiter.acquire(host)
            try:
                resp = await self._client.request(
                    method, url, headers=headers, params=params, json=json
                )
                if resp.status_code in _RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError(
                        f"retryable status {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                return resp
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                backoff = min(2 ** attempt, 30)
                logger.warning(
                    "HTTP %s %s 失敗（第 %d/%d 次）：%s；%ss 後重試",
                    method, url, attempt, self.max_retries, exc, backoff,
                )
                await asyncio.sleep(backoff)
        assert last_exc is not None
        raise last_exc
