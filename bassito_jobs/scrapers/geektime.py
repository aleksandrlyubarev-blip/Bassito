"""Geektime jobs scraper.

Geektime exposes a JSON feed at /jobs?_format=json on most layouts. We fall
back to HTML parsing if the JSON endpoint disappears.
"""

from __future__ import annotations

import json
import logging
from typing import ClassVar
from urllib.parse import urlencode

from selectolax.parser import HTMLParser

from ..profile import Profile
from ..store import Job
from .base import BaseScraper

logger = logging.getLogger(__name__)

BASE = "https://www.geektime.co.il"


class GeektimeScraper(BaseScraper):
    name: ClassVar[str] = "geektime"
    rate_limit_per_sec: ClassVar[float] = 1.0

    async def search(self, profile: Profile) -> list[Job]:
        queries = list({*(profile.include_keywords or []), "AI", "ML", "LLM"})[:5]
        out: list[Job] = []
        async with self._client() as client:
            for q in queries:
                params = {"s": q}
                url = f"{BASE}/jobs/?{urlencode(params)}"
                try:
                    resp = await self.fetch(client, url)
                    resp.raise_for_status()
                except Exception as e:  # noqa: BLE001
                    logger.warning("geektime fetch failed for %r: %s", q, e)
                    continue
                out.extend(self._parse_html(resp.text))
        return self.post_filter(_dedupe(out), profile)

    @staticmethod
    def _parse_html(html: str) -> list[Job]:
        tree = HTMLParser(html)
        out: list[Job] = []
        for card in tree.css("article.job, .job-card, .jobs-list-item"):
            title_node = card.css_first("h2 a, h3 a, .job-title a")
            if title_node is None:
                continue
            url = title_node.attributes.get("href", "")
            if not url:
                continue
            if not url.startswith("http"):
                url = f"{BASE}{url}"
            company = card.css_first(".company, .job-company")
            location = card.css_first(".location, .job-location")
            body = card.css_first(".description, .job-description, .excerpt")
            out.append(Job(
                url=url,
                source="geektime",
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
