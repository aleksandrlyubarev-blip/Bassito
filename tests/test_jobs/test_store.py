import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from bassito_jobs.store import Job, ScoredJob, top_for_profile, upsert_jobs, upsert_scores


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_upsert_dedupes_by_url(tmp_db):
    j1 = Job(url="https://x/1", source="alljobs", title="AI Eng", company="A", location="Haifa", body="x")
    j1_dup = Job(url="https://x/1", source="alljobs", title="AI Eng (updated)", company="A", location="Haifa", body="y")
    j2 = Job(url="https://x/2", source="alljobs", title="ML Eng", company="B", location="Yokneam", body="z")

    new1 = _run(upsert_jobs([j1, j1_dup], db_path=tmp_db))
    assert len(new1) == 1 and new1[0].url == "https://x/1"

    new2 = _run(upsert_jobs([j1, j2], db_path=tmp_db))
    assert len(new2) == 1 and new2[0].url == "https://x/2"


def test_scores_and_top(tmp_db):
    j1 = Job(url="https://x/1", source="alljobs", title="AI Eng", company="A", location="Haifa", body="x")
    j2 = Job(url="https://x/2", source="alljobs", title="ML Eng", company="B", location="Yokneam", body="y")
    j3 = Job(url="https://x/3", source="alljobs", title="QA", company="C", location="Tel Aviv", body="z")
    _run(upsert_jobs([j1, j2, j3], db_path=tmp_db))
    _run(upsert_scores([
        ScoredJob(job=j1, score=85, rationale="great"),
        ScoredJob(job=j2, score=60, rationale="ok"),
        ScoredJob(job=j3, score=10, rationale="no"),
    ], profile="romeoflexvision", db_path=tmp_db))

    top = _run(top_for_profile("romeoflexvision", min_score=70, limit=5, db_path=tmp_db))
    assert [s.job.url for s in top] == ["https://x/1"]

    top_all = _run(top_for_profile("romeoflexvision", min_score=0, limit=5, db_path=tmp_db))
    assert [s.job.url for s in top_all][:2] == ["https://x/1", "https://x/2"]


def test_upsert_scores_updates(tmp_db):
    j = Job(url="https://x/1", source="alljobs", title="AI Eng", body="x", location="Haifa")
    _run(upsert_jobs([j], db_path=tmp_db))
    _run(upsert_scores([ScoredJob(job=j, score=40, rationale="meh")], profile="p", db_path=tmp_db))
    _run(upsert_scores([ScoredJob(job=j, score=90, rationale="great")], profile="p", db_path=tmp_db))

    top = _run(top_for_profile("p", min_score=0, db_path=tmp_db))
    assert len(top) == 1
    assert top[0].score == 90
    assert top[0].rationale == "great"
