"""Job scrapers — one module per source."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable

from ..profile import Profile
from ..store import Job
from .base import BaseScraper
from .alljobs import AllJobsScraper
from .geektime import GeektimeScraper
from .drushim import DrushimScraper
from .flex import FlexCareersScraper
from .linkedin import LinkedInScraper

logger = logging.getLogger(__name__)


def all_scrapers(profile: Profile) -> list[BaseScraper]:
    enabled = {s.lower() for s in profile.sources_enabled}
    out: list[BaseScraper] = []
    if "alljobs" in enabled:
        out.append(AllJobsScraper())
    if "geektime" in enabled:
        out.append(GeektimeScraper())
    if "drushim" in enabled:
        out.append(DrushimScraper())
    if "flex" in enabled:
        out.append(FlexCareersScraper())
    if "linkedin" in enabled and os.getenv("BASSITO_USE_LINKEDIN", "false").lower() == "true":
        out.append(LinkedInScraper())
    return out


async def run_all(
    profile: Profile,
    *,
    per_source_timeout_s: float = 45.0,
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> list[Job]:
    """Run every enabled scraper concurrently. One scraper failing must not kill the rest."""
    scrapers = all_scrapers(profile)
    if not scrapers:
        return []

    async def _one(s: BaseScraper) -> list[Job]:
        if progress:
            await progress(f"🔎 {s.name}: searching…")
        try:
            jobs = await asyncio.wait_for(s.search(profile), timeout=per_source_timeout_s)
            if progress:
                await progress(f"✅ {s.name}: {len(jobs)} jobs")
            return jobs
        except asyncio.TimeoutError:
            logger.warning("%s scraper timed out", s.name)
            if progress:
                await progress(f"⏱ {s.name}: timeout")
            return []
        except Exception as e:  # noqa: BLE001
            logger.exception("%s scraper failed: %s", s.name, e)
            if progress:
                await progress(f"❌ {s.name}: {e}")
            return []

    chunks = await asyncio.gather(*[_one(s) for s in scrapers])
    flat: list[Job] = []
    for chunk in chunks:
        flat.extend(chunk)
    return flat


__all__ = [
    "BaseScraper",
    "AllJobsScraper",
    "GeektimeScraper",
    "DrushimScraper",
    "FlexCareersScraper",
    "LinkedInScraper",
    "all_scrapers",
    "run_all",
]
