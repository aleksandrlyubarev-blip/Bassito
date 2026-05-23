from unittest.mock import MagicMock

from bassito_jobs.profile import Profile
from bassito_jobs.ranker import has_excluded_keyword, keyword_score, prefilter, rank
from bassito_jobs.store import Job


def _profile() -> Profile:
    return Profile(
        name="romeoflexvision",
        current_role="Production Engineer",
        years_experience=8,
        skills=["python", "pytorch", "llm", "mlops"],
        preferred_stack=["pytorch", "langchain"],
        include_keywords=["AI engineer", "ML engineer"],
        exclude_keywords=["intern", "qa only"],
    )


def test_keyword_score_zero_for_unrelated():
    j = Job(url="u", source="x", title="Pastry Chef", body="bake cakes for café")
    assert keyword_score(j, _profile()) < 0.1


def test_keyword_score_high_for_overlap():
    j = Job(url="u", source="x", title="Senior AI Engineer",
            body="PyTorch, LLM stack, MLOps experience required")
    assert keyword_score(j, _profile()) > 0.3


def test_exclude_keywords_drop():
    p = _profile()
    j = Job(url="u", source="x", title="QA Only Engineer", body="manual qa")
    assert has_excluded_keyword(j, p) is True


def test_prefilter_keeps_relevant_drops_excluded():
    p = _profile()
    good = Job(url="g", source="x", title="ML Engineer", body="pytorch, llm, python")
    junk = Job(url="j", source="x", title="Cashier", body="retail")
    excluded = Job(url="e", source="x", title="AI Intern", body="pytorch llm")
    out = prefilter([good, junk, excluded], p)
    assert {j.url for j in out} == {"g"}


def test_rank_no_api_key_falls_back_to_keyword(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = _profile()
    j = Job(url="u", source="x", title="Senior AI Engineer",
            body="pytorch, llm, mlops, langchain")
    scored = rank([j], p)
    assert len(scored) == 1
    assert scored[0].score > 0
    assert "keyword-only" in scored[0].rationale


def test_rank_uses_injected_client():
    fake = MagicMock()
    fake.messages.create.return_value = MagicMock(
        content=[MagicMock(text='[{"score": 92, "rationale": "great match"}]')]
    )
    p = _profile()
    j = Job(url="u", source="x", title="Senior AI Engineer", body="pytorch llm mlops")
    scored = rank([j], p, client=fake, model="x")
    assert scored[0].score == 92
    assert scored[0].rationale == "great match"
