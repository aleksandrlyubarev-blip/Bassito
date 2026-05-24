"""Flex Ltd. careers scraper.

Flex uses Workday for its careers site. Workday exposes a stable internal JSON
API at /wday/cxs/<tenant>/<site>/jobs which accepts POST with a search payload.
URL discovered from https://flex.wd1.myworkdayjobs.com/External
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from ..profile import Profile
from ..store import Job
from .base import BaseScraper

logger = logging.getLogger(__name__)

ENDPOINT = "https://flex.wd1.myworkdayjobs.com/wday/cxs/flex/External/jobs"
JOB_PREFIX = "https://flex.wd1.myworkdayjobs.com/External"


class FlexCareersScraper(BaseScraper):
    name: ClassVar[str] = "flex"
    rate_limit_per_sec: ClassVar[float] = 1.0

    async def search(self, profile: Profile) -> list[Job]:
        searches: list[dict] = []
        queries = list({*(profile.include_keywords or []), "AI", "machine learning", "software"})[:4]
        for q in queries:
            searches.append({
                "appliedFacets": {"locationCountry": ["68ce4a89c79a01ed1eaa3aac09c7eed8"]},  # Israel
                "limit": 20,
                "offset": 0,
                "searchText": q,
            })

        out: list[Job] = []
        async with self._client() as client:
            for payload in searches:
                await self._throttle()
                try:
                    resp = await client.post(
                        ENDPOINT,
                        json=payload,
                        headers={"Accept": "application/json", "Content-Type": "application/json"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:  # noqa: BLE001
                    logger.warning("flex Workday search failed for %r: %s", payload["searchText"], e)
                    continue
                out.extend(self._parse(data))
        return self.post_filter(_dedupe(out), profile)

    @staticmethod
    def _parse(data: dict[str, Any]) -> list[Job]:
        postings = data.get("jobPostings") or []
        out: list[Job] = []
        for p in postings:
            external_path = p.get("externalPath") or ""
            url = f"{JOB_PREFIX}{external_path}" if external_path else ""
            if not url:
                continue
            location = p.get("locationsText") or p.get("locations", "") or "Israel"
            out.append(Job(
                url=url,
                source="flex",
                title=p.get("title", ""),
                company="Flex",
                location=str(location),
                body=p.get("bulletFields") and " · ".join(p["bulletFields"]) or p.get("postedOn", ""),
                posted_at=str(p.get("postedOn", "")),
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
