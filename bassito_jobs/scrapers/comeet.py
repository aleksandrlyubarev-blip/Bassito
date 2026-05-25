"""Comeet public JSON board scraper.

Comeet is the dominant ATS for Israeli tech. Each company exposes:
    https://www.comeet.co/careers-api/2.0/company/<token>/positions?details=true

The token looks like a UUID-like slug per company (visible in the careers URL).
Override the default list with BASSITO_COMEET_COMPANIES env var
("token1=Name1,token2=Name2").
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

# Tokens here are placeholders — users can swap via env. We document a few
# well-known Israeli AI companies to bootstrap; tokens MAY drift, in which
# case the scraper will simply 404 and skip.
DEFAULT_COMPANIES = [
    # (token, friendly name)
    ("76.005",  "AI21 Labs"),
    ("D5.005",  "Run:ai"),
    ("32.005",  "Pagaya"),
]

API = "https://www.comeet.co/careers-api/2.0/company/{token}/positions?details=true"

_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _HTML_TAG.sub(" ", s or "").strip()


def _parse_env(raw: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for entry in raw.split(","):
        if "=" not in entry:
            continue
        tok, name = entry.split("=", 1)
        tok, name = tok.strip(), name.strip()
        if tok and name:
            out.append((tok, name))
    return out


class ComeetScraper(BaseScraper):
    name: ClassVar[str] = "comeet"
    rate_limit_per_sec: ClassVar[float] = 1.5

    def __init__(self) -> None:
        super().__init__()
        env = os.getenv("BASSITO_COMEET_COMPANIES", "").strip()
        parsed = _parse_env(env)
        self.companies = parsed or list(DEFAULT_COMPANIES)

    async def search(self, profile: Profile) -> list[Job]:
        out: list[Job] = []
        async with self._client() as client:
            for token, name in self.companies:
                try:
                    resp = await self.fetch(client, API.format(token=token))
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:  # noqa: BLE001
                    logger.warning("comeet %s (%s) failed: %s", name, token, e)
                    continue
                out.extend(self._parse(data, company=name))
        return self.post_filter(_dedupe(out), profile)

    @staticmethod
    def _parse(data: list[Any], *, company: str) -> list[Job]:
        out: list[Job] = []
        for posting in data or []:
            url = posting.get("url_comeet_hosted_page") or posting.get("url_active_page") or ""
            if not url:
                continue
            location = posting.get("location", {}) or {}
            loc_str = ", ".join(
                str(v) for v in [location.get("name"), location.get("city"), location.get("country")] if v
            )
            body = _strip_html(posting.get("details", {}).get("job_description", ""))
            requirements = _strip_html(posting.get("details", {}).get("job_requirements", ""))
            out.append(Job(
                url=url,
                source="comeet",
                title=posting.get("name", ""),
                company=company,
                location=loc_str,
                body=(body + "\n" + requirements)[:3000],
                posted_at=str(posting.get("time_updated", "")),
                extras={"company": company, "uid": posting.get("uid")},
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
