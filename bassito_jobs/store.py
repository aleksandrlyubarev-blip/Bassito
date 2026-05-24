"""SQLite-backed dedup store for scraped jobs and LLM scores."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import aiosqlite

logger = logging.getLogger(__name__)

STORE_PATH = Path.home() / ".bassito" / "jobs" / "seen.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    url           TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    title         TEXT NOT NULL,
    company       TEXT,
    location      TEXT,
    posted_at     TEXT,
    body_hash     TEXT,
    body_excerpt  TEXT,
    first_seen    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);

CREATE TABLE IF NOT EXISTS scores (
    job_url    TEXT NOT NULL,
    profile    TEXT NOT NULL,
    score      INTEGER NOT NULL,
    rationale  TEXT,
    scored_at  TEXT NOT NULL,
    PRIMARY KEY (job_url, profile),
    FOREIGN KEY (job_url) REFERENCES jobs(url) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scores_profile_score ON scores(profile, score);
"""


@dataclass
class Job:
    url: str
    source: str
    title: str
    company: str = ""
    location: str = ""
    posted_at: str = ""
    body: str = ""
    extras: dict = field(default_factory=dict)

    @property
    def body_hash(self) -> str:
        return hashlib.sha1(self.body.encode("utf-8", errors="ignore")).hexdigest()


@dataclass
class ScoredJob:
    job: Job
    score: int
    rationale: str


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def init(db_path: Path = STORE_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def upsert_jobs(jobs: Iterable[Job], db_path: Path = STORE_PATH) -> list[Job]:
    """Insert new jobs, return the subset that were actually new."""
    await init(db_path)
    new_jobs: list[Job] = []
    now = _utcnow_iso()
    async with aiosqlite.connect(db_path) as db:
        for j in jobs:
            cur = await db.execute("SELECT 1 FROM jobs WHERE url = ?", (j.url,))
            exists = await cur.fetchone() is not None
            await cur.close()
            if exists:
                continue
            await db.execute(
                "INSERT INTO jobs(url, source, title, company, location, posted_at, body_hash, body_excerpt, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    j.url,
                    j.source,
                    j.title,
                    j.company,
                    j.location,
                    j.posted_at,
                    j.body_hash,
                    j.body[:2000],
                    now,
                ),
            )
            new_jobs.append(j)
        await db.commit()
    return new_jobs


async def upsert_scores(
    scored: Iterable[ScoredJob], profile: str, db_path: Path = STORE_PATH
) -> None:
    await init(db_path)
    now = _utcnow_iso()
    async with aiosqlite.connect(db_path) as db:
        for s in scored:
            await db.execute(
                "INSERT INTO scores(job_url, profile, score, rationale, scored_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(job_url, profile) DO UPDATE SET "
                "  score=excluded.score, rationale=excluded.rationale, scored_at=excluded.scored_at",
                (s.job.url, profile, int(s.score), s.rationale, now),
            )
        await db.commit()


async def top_for_profile(
    profile: str,
    *,
    min_score: int = 0,
    since_iso: str | None = None,
    limit: int = 20,
    db_path: Path = STORE_PATH,
) -> list[ScoredJob]:
    await init(db_path)
    query = (
        "SELECT j.url, j.source, j.title, j.company, j.location, j.posted_at, j.body_excerpt, "
        "       s.score, s.rationale "
        "FROM jobs j JOIN scores s ON s.job_url = j.url "
        "WHERE s.profile = ? AND s.score >= ?"
    )
    params: list = [profile, min_score]
    if since_iso:
        query += " AND j.first_seen >= ?"
        params.append(since_iso)
    query += " ORDER BY s.score DESC, j.first_seen DESC LIMIT ?"
    params.append(limit)

    out: list[ScoredJob] = []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(query, params) as cur:
            async for row in cur:
                job = Job(
                    url=row[0], source=row[1], title=row[2], company=row[3] or "",
                    location=row[4] or "", posted_at=row[5] or "", body=row[6] or "",
                )
                out.append(ScoredJob(job=job, score=row[7], rationale=row[8] or ""))
    return out
