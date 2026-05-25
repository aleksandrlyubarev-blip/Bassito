"""Lever public JSON board scraper.

API: https://api.lever.co/v0/postings/<company>?mode=json

Each Lever-hosted company has a single canonical "slug" used by both their
public careers site and the JSON endpoint.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, ClassVar

from ..profile import Profile
from ..store import Job
from .base import BaseScraper

logger = logging.getLogger(__name__)

DEFAULT_COMPANIES = [
    "lightricks",
    "ridgesecurity",
    "verbit",
    "celonis",
    "stripe",  # NYC/SF mostly — geo filter will drop most
]

API = "https://api.lever.co/v0/postings/{company}?mode=json"

_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _HTML_TAG.sub(" ", s or "").strip()


class LeverScraper(BaseScraper):
    name: ClassVar[str] = "lever"
    rate_limit_per_sec: ClassVar[float] = 2.0

    def __init__(self) -> None:
        super().__init__()
        env = os.getenv("BASSITO_LEVER_COMPANIES", "").strip()
        self.companies = [c.strip() for c in env.split(",") if c.strip()] or list(DEFAULT_COMPANIES)

    async def search(self, profile: Profile) -> list[Job]:
        out: list[Job] = []
        async with self._client() as client:
            for company in self.companies:
                try:
                    resp = await self.fetch(client, API.format(company=company))
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:  # noqa: BLE001
                    logger.warning("lever %s failed: %s", company, e)
                    continue
                out.extend(self._parse(data, company=company))
        return self.post_filter(_dedupe(out), profile)

    @staticmethod
    def _parse(data: list[Any], *, company: str) -> list[Job]:
        out: list[Job] = []
        for posting in data:
            url = posting.get("hostedUrl") or posting.get("applyUrl") or ""
            if not url:
                continue
            cats = posting.get("categories", {}) or {}
            location = cats.get("location", "")
            body_parts = [
                posting.get("descriptionPlain") or _strip_html(posting.get("description", "")),
            ]
            for sec in posting.get("lists", []) or []:
                body_parts.append(sec.get("text", "") or _strip_html(sec.get("content", "")))
            body = " ".join(p for p in body_parts if p)
            out.append(Job(
                url=url,
                source="lever",
                title=posting.get("text", ""),
                company=company,
                location=location,
                body=body[:3000],
                posted_at=str(posting.get("createdAt", "")),
                extras={"company": company, "lever_id": posting.get("id")},
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
