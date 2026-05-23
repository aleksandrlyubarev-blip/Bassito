"""Base scraper: shared httpx client, rate limiting, geo post-filter."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import ClassVar

import httpx

from .. import geo as geo_mod
from ..profile import Profile
from ..store import Job

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)


class BaseScraper(ABC):
    name: ClassVar[str] = "base"
    rate_limit_per_sec: ClassVar[float] = 1.0
    timeout_s: ClassVar[float] = 25.0

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_ts = 0.0

    async def _throttle(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            interval = 1.0 / max(self.rate_limit_per_sec, 0.01)
            wait = interval - (now - self._last_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_ts = loop.time()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout_s,
            headers={"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9,he;q=0.8"},
            follow_redirects=True,
        )

    async def fetch(self, client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
        await self._throttle()
        logger.debug("%s GET %s", self.name, url)
        return await client.get(url, **kwargs)

    @abstractmethod
    async def search(self, profile: Profile) -> list[Job]:
        """Return jobs already filtered by geo and exclude_keywords."""

    def post_filter(self, jobs: list[Job], profile: Profile) -> list[Job]:
        out: list[Job] = []
        for j in jobs:
            if not geo_mod.passes(j.location, j.company, profile.geo):
                continue
            blob = f"{j.title}\n{j.body}".lower()
            if any(ex.lower() in blob for ex in profile.exclude_keywords if ex):
                continue
            out.append(j)
        return out
