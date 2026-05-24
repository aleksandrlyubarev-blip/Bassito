"""
bassito_jobs — AI engineering job search for the Bassito Telegram bot.

Public API:
    run_search(profile, force_refresh=False) -> SearchResult
    load_profile(name) -> Profile | None
    save_profile(profile) -> None
"""

from .profile import Profile, load_profile, save_profile
from .runner import JobSearchRunner, SearchResult, run_search

__all__ = [
    "Profile",
    "JobSearchRunner",
    "SearchResult",
    "run_search",
    "load_profile",
    "save_profile",
]
