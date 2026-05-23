"""Drushim.co.il scraper — mass Israeli job board."""

from __future__ import annotations

import logging
from typing import ClassVar
from urllib.parse import urlencode

from selectolax.parser import HTMLParser

from ..profile import Profile
from ..store import Job
from .base import BaseScraper

logger = logging.getLogger(__name__)

BASE = "https://www.drushim.co.il"


class DrushimScraper(BaseScraper):
    name: ClassVar[str] = "drushim"
    rate_limit_per_sec: ClassVar[float] = 0.8

    async def search(self, profile: Profile) -> list[Job]:
        queries = list({*(profile.include_keywords or []), "AI", "machine learning", "data scientist"})[:5]
        out: list[Job] = []
        async with self._client() as client:
            for q in queries:
                params = {"keyword": q, "scopeIds": "1,2"}
                url = f"{BASE}/jobs/?{urlencode(params)}"
                try:
                    resp = await self.fetch(client, url)
                    resp.raise_for_status()
                except Exception as e:  # noqa: BLE001
                    logger.warning("drushim fetch failed for %r: %s", q, e)
                    continue
                out.extend(self._parse(resp.text))
        return self.post_filter(_dedupe(out), profile)

    @staticmethod
    def _parse(html: str) -> list[Job]:
        tree = HTMLParser(html)
        out: list[Job] = []
        for card in tree.css("section.job, article.job, .job-info-container, .job-result"):
            title_node = card.css_first(".job-title a, h3 a, h2 a")
            if title_node is None:
                continue
            url = title_node.attributes.get("href", "")
            if not url:
                continue
            if not url.startswith("http"):
                url = f"{BASE}{url}"
            company = card.css_first(".company-name, .employer-name, .job-employer")
            location = card.css_first(".location, .job-location, .job-area")
            body = card.css_first(".description, .job-description, .desc")
            out.append(Job(
                url=url,
                source="drushim",
                title=title_node.text(strip=True),
                company=company.text(strip=True) if company else "",
                location=(location.text(strip=True) if location else "") + " Israel",
                body=body.text(strip=True) if body else "",
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
