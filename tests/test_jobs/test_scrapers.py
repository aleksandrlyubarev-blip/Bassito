"""Scraper tests using static HTML fixtures and JSON snapshots."""

from __future__ import annotations

from pathlib import Path

from bassito_jobs.profile import Profile
from bassito_jobs.scrapers.alljobs import AllJobsScraper
from bassito_jobs.scrapers.comeet import ComeetScraper
from bassito_jobs.scrapers.flex import FlexCareersScraper
from bassito_jobs.scrapers.geektime import GeektimeScraper
from bassito_jobs.scrapers.greenhouse import GreenhouseScraper
from bassito_jobs.scrapers.lever import LeverScraper

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


def test_greenhouse_parse_strips_html():
    import json as _json
    data = _json.loads((FIXTURES / "greenhouse_lemonade.json").read_text(encoding="utf-8"))
    jobs = GreenhouseScraper._parse(data, board="lemonade")
    assert len(jobs) == 2
    assert jobs[0].source == "greenhouse"
    assert jobs[0].company == "lemonade"
    assert "<p>" not in jobs[0].body
    assert "PyTorch" in jobs[0].body


def test_lever_parse_concatenates_lists():
    import json as _json
    data = _json.loads((FIXTURES / "lever_lightricks.json").read_text(encoding="utf-8"))
    jobs = LeverScraper._parse(data, company="lightricks")
    assert len(jobs) == 2
    ml = jobs[0]
    assert ml.source == "lever"
    assert "diffusion" in ml.body
    assert ml.location == "Jerusalem, Israel"


def test_comeet_parse_merges_description_and_requirements():
    import json as _json
    data = _json.loads((FIXTURES / "comeet_ai21.json").read_text(encoding="utf-8"))
    jobs = ComeetScraper._parse(data, company="AI21 Labs")
    assert len(jobs) == 2
    j = jobs[0]
    assert j.source == "comeet"
    assert j.company == "AI21 Labs"
    assert "PyTorch" in j.body
    assert "Lead LLM" in j.body
