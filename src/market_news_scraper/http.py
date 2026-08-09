from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx


class SourceHTTPError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class PerHostCadence:
    """Serialize requests per host and enforce a delay after each request."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._next_allowed: dict[str, float] = {}

    @asynccontextmanager
    async def slot(self, host: str, interval_seconds: float) -> AsyncIterator[None]:
        normalized_host = host.lower().rstrip(".")
        async with self._locks[normalized_host]:
            delay = self._next_allowed.get(normalized_host, self._clock()) - self._clock()
            if delay > 0:
                await self._sleep(delay)
            try:
                yield
            finally:
                self._next_allowed[normalized_host] = self._clock() + interval_seconds


class CachedFeedClient:
    def __init__(
        self,
        *,
        cache_dir: Path,
        cache_seconds: int,
        user_agent: str,
        timeout_seconds: float,
        max_concurrency: int,
        sec_min_interval_seconds: float,
        issuer_min_interval_seconds: float,
        cadence: PerHostCadence | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.cache_seconds = cache_seconds
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.sec_min_interval_seconds = sec_min_interval_seconds
        self.issuer_min_interval_seconds = issuer_min_interval_seconds
        self.cadence = cadence or PerHostCadence()

    def _interval_for_host(self, host: str) -> float:
        return (
            self.sec_min_interval_seconds
            if host in {"sec.gov", "www.sec.gov"}
            else self.issuer_min_interval_seconds
        )

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, url: str) -> bytes | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            saved_at = datetime.fromisoformat(item["saved_at"])
            age = (datetime.now(UTC) - saved_at).total_seconds()
            return bytes.fromhex(item["body_hex"]) if age <= self.cache_seconds else None
        except (OSError, ValueError, KeyError):
            return None

    def _write_cache(self, url: str, body: bytes) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(url)
        temporary = path.with_suffix(".tmp")
        payload = {"saved_at": datetime.now(UTC).isoformat(), "body_hex": body.hex()}
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(path)

    async def get(self, url: str, allowed_hosts: set[str]) -> bytes:
        cached = self._read_cache(url)
        if cached is not None:
            return cached
        async with self.semaphore, httpx.AsyncClient(
            timeout=self.timeout_seconds,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/atom+xml, application/rss+xml, application/xml",
            },
            follow_redirects=False,
        ) as client:
            current = url
            for _ in range(4):
                host = (urlsplit(current).hostname or "").lower()
                if host not in allowed_hosts:
                    raise SourceHTTPError(
                        "blocked",
                        f"redirect host {host!r} is not allowlisted",
                        False,
                    )
                try:
                    async with self.cadence.slot(host, self._interval_for_host(host)):
                        response = await client.get(current)
                except httpx.TimeoutException as exc:
                    raise SourceHTTPError("timeout", "source request timed out", True) from exc
                except httpx.HTTPError as exc:
                    raise SourceHTTPError("unavailable", "source request failed", True) from exc
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise SourceHTTPError("malformed", "redirect omitted location", False)
                    current = urljoin(current, location)
                    continue
                if response.status_code == 429:
                    raise SourceHTTPError("rate_limited", "source rate limited the request", True)
                if response.status_code >= 500:
                    raise SourceHTTPError("unavailable", "source server was unavailable", True)
                if response.status_code >= 400:
                    raise SourceHTTPError(
                        "blocked",
                        f"source returned HTTP {response.status_code}",
                        False,
                    )
                body = response.content
                self._write_cache(url, body)
                return body
            raise SourceHTTPError("blocked", "too many redirects", False)
