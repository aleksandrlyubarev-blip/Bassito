"""AllJobs.co.il scraper.

Public search URL (no auth):
    https://www.alljobs.co.il/SearchResultsGuest.aspx?...&page=N

The site is HTML-heavy and structure changes frequently. We do best-effort
parsing: any failure on a single posting is logged + skipped. Keep selectors
loose enough to survive layout tweaks.
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

BASE = "https://www.alljobs.co.il"


class AllJobsScraper(BaseScraper):
    name: ClassVar[str] = "alljobs"
    rate_limit_per_sec: ClassVar[float] = 0.8

    async def search(self, profile: Profile) -> list[Job]:
        queries = list({*(profile.include_keywords or []), "AI engineer", "machine learning"})[:6]
        results: list[Job] = []
        async with self._client() as client:
            for q in queries:
                params = {"freetxt": q, "region": "1,3"}  # 1=Haifa+North, 3=Center
                url = f"{BASE}/SearchResultsGuest.aspx?{urlencode(params)}"
                try:
                    resp = await self.fetch(client, url)
                    resp.raise_for_status()
                except Exception as e:  # noqa: BLE001
                    logger.warning("alljobs fetch failed for %r: %s", q, e)
                    continue
                results.extend(self._parse(resp.text, query=q))
        return self.post_filter(_dedupe(results), profile)

    @staticmethod
    def _parse(html: str, *, query: str) -> list[Job]:
        tree = HTMLParser(html)
        out: list[Job] = []
        for card in tree.css(".job-content-top, .job-item, article.job-listing"):
            title_node = card.css_first("a.job-content-top-title, .job-title a, h3 a")
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            href = title_node.attributes.get("href", "")
            if not href:
                continue
            url = href if href.startswith("http") else f"{BASE}{href}"
            company_node = card.css_first(".job-content-top-corporation, .company-name")
            location_node = card.css_first(".job-content-top-area, .location, .job-location")
            body_node = card.css_first(".job-content-top-description, .job-description, .description")
            out.append(Job(
                url=url,
                source="alljobs",
                title=title,
                company=company_node.text(strip=True) if company_node else "",
                location=(location_node.text(strip=True) if location_node else "") + " Israel",
                body=body_node.text(strip=True) if body_node else "",
                extras={"query": query},
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
