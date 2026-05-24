"""Two-stage job ranker: cheap keyword pre-filter + Anthropic LLM scoring."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .profile import Profile
from .store import Job, ScoredJob

logger = logging.getLogger(__name__)

RANK_SYSTEM = """You score job postings 0-100 by how strongly they represent a CAREER PROMOTION for the given candidate.

100 = clearly senior/lead AI engineering role that beats the candidate's current position (Flex factory engineer in Israel) on scope, seniority, comp, or impact.
70  = solid match, lateral or modest step up.
40  = adjacent or partially relevant.
0   = unrelated, junior, wrong stack, intern, QA-only.

Job descriptions may be in Hebrew or English. Score by meaning, not language.

Return STRICT JSON array, one object per job, in the same order:
[{"score": <int>, "rationale": "<<=120 chars, why this score>"}]
No prose, no markdown fence."""


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[a-zA-Z][a-zA-Z+#.-]{1,}", text or "")}


def keyword_score(job: Job, profile: Profile) -> float:
    """Cheap overlap score 0..1. Used to skip LLM calls on obvious misses."""
    blob = " ".join([job.title, job.company, job.body])
    job_tokens = _tokens(blob)
    if not job_tokens:
        return 0.0

    wanted = {t.lower() for t in profile.skills + profile.include_keywords + profile.preferred_stack}
    wanted_tokens: set[str] = set()
    for w in wanted:
        wanted_tokens.update(_tokens(w))
    if not wanted_tokens:
        return 0.5  # no opinion → let the LLM decide

    hits = len(job_tokens & wanted_tokens)
    return min(1.0, hits / max(3, len(wanted_tokens) * 0.15))


def has_excluded_keyword(job: Job, profile: Profile) -> bool:
    blob = f"{job.title}\n{job.body}".lower()
    return any(ex.lower() in blob for ex in profile.exclude_keywords if ex)


def prefilter(jobs: list[Job], profile: Profile, *, threshold: float = 0.15) -> list[Job]:
    """Drop obvious misses before paying for LLM calls."""
    out = []
    for j in jobs:
        if has_excluded_keyword(j, profile):
            continue
        if keyword_score(j, profile) < threshold:
            continue
        out.append(j)
    return out


def _job_dict(job: Job) -> dict:
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "excerpt": (job.body or "")[:1500],
    }


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _profile_brief(profile: Profile) -> str:
    return json.dumps({
        "current_role": profile.current_role,
        "years_experience": profile.years_experience,
        "skills": profile.skills,
        "seniority": profile.seniority,
        "preferred_stack": profile.preferred_stack,
        "include_keywords": profile.include_keywords,
    }, ensure_ascii=False)


def _score_batch_llm(
    batch: list[Job],
    profile: Profile,
    *,
    client: Any,
    model: str,
) -> list[ScoredJob]:
    user_msg = (
        f"CANDIDATE PROFILE:\n{_profile_brief(profile)}\n\n"
        f"JOBS ({len(batch)}):\n{json.dumps([_job_dict(j) for j in batch], ensure_ascii=False)}"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=1500,
        system=RANK_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = resp.content[0].text if resp.content else "[]"
    try:
        parsed = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as e:
        logger.warning("ranker non-JSON response: %s\n%s", e, raw[:500])
        return [ScoredJob(job=j, score=0, rationale="LLM parse error") for j in batch]

    out: list[ScoredJob] = []
    for j, item in zip(batch, parsed):
        score = max(0, min(100, int(item.get("score", 0))))
        out.append(ScoredJob(job=j, score=score, rationale=str(item.get("rationale", ""))[:200]))
    # If the LLM returned fewer items than asked, score the tail with 0.
    for j in batch[len(parsed):]:
        out.append(ScoredJob(job=j, score=0, rationale="missing from LLM output"))
    return out


def rank(
    jobs: list[Job],
    profile: Profile,
    *,
    model: str = "claude-haiku-4-5-20251001",
    batch_size: int = 10,
    client: Any | None = None,
) -> list[ScoredJob]:
    """Rank `jobs` against `profile`. Pre-filters, then batches LLM calls."""
    candidates = prefilter(jobs, profile)
    if not candidates:
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key and client is None:
        logger.warning("ANTHROPIC_API_KEY not set; falling back to keyword score only")
        return [
            ScoredJob(job=j, score=int(keyword_score(j, profile) * 100),
                      rationale="keyword-only (no Anthropic key)")
            for j in candidates
        ]

    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

    scored: list[ScoredJob] = []
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        try:
            scored.extend(_score_batch_llm(batch, profile, client=client, model=model))
        except Exception as e:  # noqa: BLE001
            logger.error("ranker batch failed: %s", e)
            scored.extend(
                ScoredJob(job=j, score=0, rationale=f"ranker error: {e}") for j in batch
            )
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored
