"""JobSearchRunner — orchestrates scrapers → store → ranker for one profile run."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .profile import Profile, load_profile
from .ranker import rank
from .scrapers import run_all
from .store import Job, ScoredJob, top_for_profile, upsert_jobs, upsert_scores

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], Awaitable[None]]


@dataclass
class SearchResult:
    profile: str
    scraped: int = 0
    new: int = 0
    scored: int = 0
    top: list[ScoredJob] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def format_telegram(self, *, limit: int = 10, min_score: int = 0) -> str:
        head = (
            f"🔎 *Find Boost — {self.profile}*\n"
            f"scraped {self.scraped} · new {self.new} · scored {self.scored}\n\n"
        )
        body_lines: list[str] = []
        for i, s in enumerate(self.top[:limit], start=1):
            if s.score < min_score:
                continue
            j = s.job
            loc = f" · {j.location}" if j.location else ""
            body_lines.append(
                f"{i}. *{s.score}* — [{j.title}]({j.url})\n"
                f"   {j.company or 'unknown'}{loc} · _{j.source}_\n"
                f"   {s.rationale}".rstrip()
            )
        if not body_lines:
            body_lines.append("_Нет подходящих вакансий._")
        return head + "\n\n".join(body_lines)


class JobSearchRunner:
    def __init__(self, progress: ProgressCb | None = None) -> None:
        self.on_progress = progress

    async def _emit(self, msg: str) -> None:
        if self.on_progress:
            try:
                await self.on_progress(msg)
            except Exception:  # noqa: BLE001
                logger.exception("progress callback raised")

    async def run(
        self,
        profile: Profile,
        *,
        top_n: int = 10,
        min_score: int = 0,
        only_new: bool = False,
    ) -> SearchResult:
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result = SearchResult(profile=profile.name, started_at=started)

        await self._emit(f"🚀 Searching jobs for `{profile.name}`…")
        jobs: list[Job] = await run_all(profile, progress=self._emit)
        result.scraped = len(jobs)
        await self._emit(f"📦 Scraped {len(jobs)} postings, storing…")

        new_jobs = await upsert_jobs(jobs)
        result.new = len(new_jobs)
        to_score = new_jobs if only_new else jobs
        await self._emit(f"🧠 Ranking {len(to_score)} jobs…")

        scored: list[ScoredJob] = rank(to_score, profile)
        result.scored = len(scored)
        if scored:
            await upsert_scores(scored, profile.name)

        result.top = await top_for_profile(profile.name, min_score=min_score, limit=top_n)
        result.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        await self._emit(f"🏁 Done. Top {len(result.top)} returned.")
        return result


async def run_search(
    profile_name: str,
    *,
    progress: ProgressCb | None = None,
    top_n: int = 10,
    min_score: int = 0,
    only_new: bool = False,
) -> SearchResult:
    profile = load_profile(profile_name)
    if profile is None:
        raise FileNotFoundError(
            f"Profile '{profile_name}' not found. Run /find_boost_profile {profile_name} and attach your CV."
        )
    runner = JobSearchRunner(progress=progress)
    return await runner.run(profile, top_n=top_n, min_score=min_score, only_new=only_new)
