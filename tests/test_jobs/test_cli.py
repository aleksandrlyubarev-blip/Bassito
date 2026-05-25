import sys

import pytest

from bassito_jobs.__main__ import main


def test_cli_missing_profile_returns_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("bassito_jobs.profile.PROFILES_ROOT", tmp_path)
    code = main(["does_not_exist"])
    assert code == 2
    err = capsys.readouterr().err
    assert "Profile 'does_not_exist' not found" in err


def test_cli_top_only_empty_store(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("bassito_jobs.profile.PROFILES_ROOT", tmp_path)
    monkeypatch.setattr("bassito_jobs.store.STORE_PATH", tmp_path / "seen.db")

    from bassito_jobs.profile import Profile, save_profile
    save_profile(Profile(name="t"))

    code = main(["t", "--top-only"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Find Boost — t" in out
