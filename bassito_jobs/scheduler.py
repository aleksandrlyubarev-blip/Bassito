"""Daily-digest scheduler for watched profiles.

Each profile that the user `/find_boost_watch <name>`'d gets a JSON file at
~/.bassito/profiles/<name>/watch.json. The scheduler scans the profiles
directory, registers an APScheduler cron job per profile, and on tick runs a
fresh search and posts only new high-score jobs back to the user's chat.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .profile import PROFILES_ROOT, load_profile
from .runner import run_search
from .store import top_for_profile

logger = logging.getLogger(__name__)

DEFAULT_HOUR = int(os.getenv("BASSITO_JOBS_DIGEST_HOUR", "9"))
DEFAULT_MIN_SCORE = int(os.getenv("BASSITO_JOBS_MIN_SCORE", "70"))
TIMEZONE = "Asia/Jerusalem"

_scheduler: AsyncIOScheduler | None = None


def watch_path(profile_name: str) -> Path:
    return PROFILES_ROOT / profile_name / "watch.json"


def load_watch(profile_name: str) -> dict[str, Any] | None:
    p = watch_path(profile_name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("watch.json for %s is corrupt", profile_name)
        return None


def save_watch(profile_name: str, data: dict[str, Any]) -> None:
    p = watch_path(profile_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def remove_watch(profile_name: str) -> bool:
    p = watch_path(profile_name)
    if p.exists():
        p.unlink()
        return True
    return False


def _job_id(profile_name: str) -> str:
    return f"digest:{profile_name}"


async def _run_digest(bot: Any, profile_name: str) -> None:
    watch = load_watch(profile_name)
    if not watch:
        logger.info("digest skipped: no watch for %s", profile_name)
        return
    profile = load_profile(profile_name)
    if profile is None:
        logger.warning("digest skipped: profile %s missing", profile_name)
        return

    chat_id = watch["chat_id"]
    min_score = int(watch.get("min_score", DEFAULT_MIN_SCORE))
    since = watch.get("since_iso")

    try:
        await run_search(profile_name, top_n=30, min_score=min_score, only_new=True)
    except Exception as e:  # noqa: BLE001
        logger.exception("digest run failed: %s", e)
        await bot.send_message(chat_id=chat_id, text=f"❌ Daily digest failed: {e}")
        return

    top = await top_for_profile(profile_name, min_score=min_score, since_iso=since, limit=10)
    if not top:
        await bot.send_message(chat_id=chat_id, text=f"📭 No new ≥{min_score} hits today for `{profile_name}`.", parse_mode="Markdown")
    else:
        lines = [f"📬 *Daily digest — {profile_name}* (≥{min_score})"]
        for i, s in enumerate(top, start=1):
            j = s.job
            loc = f" · {j.location}" if j.location else ""
            lines.append(
                f"{i}. *{s.score}* — [{j.title}]({j.url})\n"
                f"   {j.company or 'unknown'}{loc} · _{j.source}_\n"
                f"   {s.rationale}".rstrip()
            )
        await bot.send_message(
            chat_id=chat_id,
            text="\n\n".join(lines),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    watch["since_iso"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_watch(profile_name, watch)


def _register(bot: Any, profile_name: str) -> None:
    global _scheduler
    if _scheduler is None:
        return
    watch = load_watch(profile_name)
    if not watch:
        return
    cron_expr = watch.get("cron", f"0 {DEFAULT_HOUR} * * *")
    try:
        trigger = CronTrigger.from_crontab(cron_expr, timezone=TIMEZONE)
    except ValueError:
        logger.warning("bad cron %r for %s; using default", cron_expr, profile_name)
        trigger = CronTrigger(hour=DEFAULT_HOUR, timezone=TIMEZONE)
    _scheduler.add_job(
        _run_digest,
        trigger=trigger,
        args=[bot, profile_name],
        id=_job_id(profile_name),
        replace_existing=True,
    )
    logger.info("scheduled digest for %s (%s)", profile_name, cron_expr)


def unregister(profile_name: str) -> None:
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(_job_id(profile_name))
    except Exception:  # noqa: BLE001
        pass


def add_watch(bot: Any, profile_name: str, chat_id: int, *, cron: str | None = None, min_score: int | None = None) -> None:
    data = load_watch(profile_name) or {}
    data["chat_id"] = chat_id
    data["cron"] = cron or data.get("cron", f"0 {DEFAULT_HOUR} * * *")
    data["min_score"] = int(min_score if min_score is not None else data.get("min_score", DEFAULT_MIN_SCORE))
    data.setdefault("since_iso", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    save_watch(profile_name, data)
    _register(bot, profile_name)


async def start(app: Any) -> None:
    """Boot the scheduler and re-register every existing watch.json."""
    global _scheduler
    if _scheduler is not None:
        return
    PROFILES_ROOT.mkdir(parents=True, exist_ok=True)
    _scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    _scheduler.start()
    bot = app.bot
    for profile_dir in PROFILES_ROOT.iterdir():
        if profile_dir.is_dir() and (profile_dir / "watch.json").exists():
            _register(bot, profile_dir.name)
    logger.info("job-search scheduler started")
