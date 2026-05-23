"""LinkedIn public guest jobs scraper.

Disabled by default; enable with BASSITO_USE_LINKEDIN=true.

⚠️ Scraping LinkedIn may violate their ToS. We use the public guest search
endpoint that requires no auth, rate-limit aggressively, and the user must
opt-in via env. Expect this scraper to break periodically.
"""

from __future__ import annotations

import logging
from typing import ClassVar
from urllib.parse import urlencode

from selectolax.parser import HTMLParser

from ..profile import Profile
from ..store import Job
from .base import BaseScraper

logger = logging.getLogger(__name__)

GUEST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


class LinkedInScraper(BaseScraper):
    name: ClassVar[str] = "linkedin"
    rate_limit_per_sec: ClassVar[float] = 0.5  # extra cautious

    async def search(self, profile: Profile) -> list[Job]:
        queries = list({*(profile.include_keywords or []), "AI engineer", "ML engineer"})[:4]
        out: list[Job] = []
        async with self._client() as client:
            for q in queries:
                params = {
                    "keywords": q,
                    "location": "Israel",
                    "f_TPR": "r604800",  # last 7 days
                    "start": 0,
                }
                url = f"{GUEST_URL}?{urlencode(params)}"
                try:
                    resp = await self.fetch(client, url)
                    resp.raise_for_status()
                except Exception as e:  # noqa: BLE001
                    logger.warning("linkedin fetch failed for %r: %s", q, e)
                    continue
                out.extend(self._parse(resp.text))
        return self.post_filter(_dedupe(out), profile)

    @staticmethod
    def _parse(html: str) -> list[Job]:
        tree = HTMLParser(html)
        out: list[Job] = []
        for card in tree.css("li, div.base-card"):
            title_node = card.css_first("h3.base-search-card__title, h3")
            link_node = card.css_first("a.base-card__full-link, a")
            if title_node is None or link_node is None:
                continue
            url = link_node.attributes.get("href", "").split("?", 1)[0]
            if "linkedin.com/jobs/view" not in url:
                continue
            company = card.css_first("h4.base-search-card__subtitle, .base-search-card__subtitle a")
            location = card.css_first(".job-search-card__location")
            out.append(Job(
                url=url,
                source="linkedin",
                title=title_node.text(strip=True),
                company=company.text(strip=True) if company else "",
                location=location.text(strip=True) if location else "Israel",
                body="",
            ))
        return out


def _dedupe(jobs: list[Job]) -> list[Job]:
    seen: set[str] = set()
    out: list[Job] = []
    for j in jobs:
        if j.url in seen:
            continue
        seen.add(j.url)
        out.append(j)
    return out
