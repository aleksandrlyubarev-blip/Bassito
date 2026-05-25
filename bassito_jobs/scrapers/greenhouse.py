"""Greenhouse public JSON board scraper.

Every Greenhouse-hosted company exposes:
    https://boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true

Boards covered by default are configurable via env BASSITO_GREENHOUSE_BOARDS
(comma-separated tokens) and default to a small starter set of IL-active
companies. Add more by appending to the env list.
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

DEFAULT_BOARDS = [
    "lemonade",
    "snyk",
    "wiz",
    "monday",
    "deelinc",
    "papayaglobal",
    "rapyd",
]

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"

_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _HTML_TAG.sub(" ", s or "").strip()


class GreenhouseScraper(BaseScraper):
    name: ClassVar[str] = "greenhouse"
    rate_limit_per_sec: ClassVar[float] = 2.0

    def __init__(self) -> None:
        super().__init__()
        env_boards = os.getenv("BASSITO_GREENHOUSE_BOARDS", "").strip()
        self.boards = [b.strip() for b in env_boards.split(",") if b.strip()] or list(DEFAULT_BOARDS)

    async def search(self, profile: Profile) -> list[Job]:
        out: list[Job] = []
        async with self._client() as client:
            for board in self.boards:
                url = API.format(board=board) + "?content=true"
                try:
                    resp = await self.fetch(client, url)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:  # noqa: BLE001
                    logger.warning("greenhouse %s failed: %s", board, e)
                    continue
                out.extend(self._parse(data, board=board))
        return self.post_filter(_dedupe(out), profile)

    @staticmethod
    def _parse(data: dict[str, Any], *, board: str) -> list[Job]:
        out: list[Job] = []
        for posting in data.get("jobs", []):
            url = posting.get("absolute_url") or ""
            if not url:
                continue
            location = (posting.get("location") or {}).get("name", "")
            body = _strip_html(posting.get("content", "")) or ""
            out.append(Job(
                url=url,
                source="greenhouse",
                title=posting.get("title", ""),
                company=board,
                location=location,
                body=body[:3000],
                posted_at=posting.get("updated_at", ""),
                extras={"board": board, "gh_id": posting.get("id")},
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
