import json
from unittest.mock import MagicMock, patch

import pytest

from bassito_jobs.profile import Profile, extract_profile_from_pdf, load_profile, save_profile

# pypdf imports `cryptography`, which can fail in some environments. We mock
# extract_text_from_pdf to avoid pulling pypdf for these unit tests.


def test_profile_roundtrip_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr("bassito_jobs.profile.PROFILES_ROOT", tmp_path)
    p = Profile(
        name="romeoflexvision",
        current_role="Senior AI Engineer",
        years_experience=8,
        skills=["python", "pytorch"],
        include_keywords=["AI engineer"],
    )
    path = save_profile(p)
    assert path.exists()

    loaded = load_profile("romeoflexvision")
    assert loaded is not None
    assert loaded.current_role == "Senior AI Engineer"
    assert loaded.skills == ["python", "pytorch"]
    assert loaded.geo.center == (32.6857, 35.2354)


def test_load_profile_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("bassito_jobs.profile.PROFILES_ROOT", tmp_path)
    assert load_profile("nope") is None


@patch("bassito_jobs.profile.extract_text_from_pdf",
       return_value="Senior AI Engineer at Flex; Python, PyTorch, LLM")
def test_extract_profile_from_pdf_uses_injected_client(_text, tmp_path):
    fake = MagicMock()
    payload = {
        "current_role": "Senior AI Engineer",
        "years_experience": 8,
        "skills": ["python", "pytorch", "llm"],
        "seniority": ["senior", "lead"],
        "preferred_stack": ["pytorch", "langchain"],
        "include_keywords": ["AI engineer"],
        "exclude_keywords": ["intern"],
    }
    fake.messages.create.return_value = MagicMock(
        content=[MagicMock(text=json.dumps(payload))]
    )

    profile = extract_profile_from_pdf("romeoflexvision", tmp_path / "cv.pdf", client=fake)
    assert profile.name == "romeoflexvision"
    assert profile.current_role == "Senior AI Engineer"
    assert "pytorch" in profile.skills


@patch("bassito_jobs.profile.extract_text_from_pdf", return_value="Some CV text")
def test_extract_profile_handles_fenced_json(_text, tmp_path):
    fake = MagicMock()
    fake.messages.create.return_value = MagicMock(
        content=[MagicMock(text='```json\n{"current_role": "X", "skills": ["py"]}\n```')]
    )
    profile = extract_profile_from_pdf("n", tmp_path / "cv.pdf", client=fake)
    assert profile.current_role == "X"
    assert profile.skills == ["py"]


@patch("bassito_jobs.profile.extract_text_from_pdf", return_value="text")
def test_extract_profile_no_api_key_returns_stub(_text, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    profile = extract_profile_from_pdf("romeoflexvision", tmp_path / "cv.pdf")
    assert profile.name == "romeoflexvision"
    assert "ANTHROPIC_API_KEY" in profile.current_role
