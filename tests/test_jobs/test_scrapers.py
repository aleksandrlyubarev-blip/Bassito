"""Scraper tests using static HTML fixtures and JSON snapshots."""

from __future__ import annotations

from pathlib import Path

from bassito_jobs.profile import Profile
from bassito_jobs.scrapers.alljobs import AllJobsScraper
from bassito_jobs.scrapers.flex import FlexCareersScraper
from bassito_jobs.scrapers.geektime import GeektimeScraper

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "scrapers"


def _profile(sources: list[str]) -> Profile:
    return Profile(
        name="t",
        skills=["python", "pytorch", "llm"],
        include_keywords=["AI engineer"],
        sources_enabled=sources,
    )


def test_alljobs_parse():
    html = (FIXTURES / "alljobs_listing.html").read_text(encoding="utf-8")
    jobs = AllJobsScraper._parse(html, query="AI")
    assert len(jobs) >= 1
    j = jobs[0]
    assert j.source == "alljobs"
    assert j.title
    assert j.url.startswith("https://www.alljobs.co.il")


def test_geektime_parse():
    html = (FIXTURES / "geektime_listing.html").read_text(encoding="utf-8")
    jobs = GeektimeScraper._parse_html(html)
    assert len(jobs) >= 1
    assert jobs[0].source == "geektime"


def test_flex_workday_parse():
    import json as _json
    data = _json.loads((FIXTURES / "flex_workday.json").read_text(encoding="utf-8"))
    jobs = FlexCareersScraper._parse(data)
    assert len(jobs) >= 1
    j = jobs[0]
    assert j.source == "flex"
    assert j.company == "Flex"
    assert "myworkdayjobs.com" in j.url
