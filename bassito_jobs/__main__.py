"""CLI: `python -m bassito_jobs <profile> [--top N] [--min-score N] [--source ...]`.

Runs a one-shot search without the Telegram bot. Useful on a local Mac to
iterate on profiles, debug scrapers, and verify the ranking output.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .profile import load_profile
from .runner import JobSearchRunner
from .store import top_for_profile


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _print_result(result, *, min_score: int) -> None:
    print(f"\n=== Find Boost — {result.profile} ===")
    print(f"scraped {result.scraped} · new {result.new} · scored {result.scored}\n")
    shown = 0
    for i, s in enumerate(result.top, start=1):
        if s.score < min_score:
            continue
        shown += 1
        j = s.job
        loc = f" · {j.location}" if j.location else ""
        print(f"{i:>2}. [{s.score:>3}] {j.title}")
        print(f"     {j.company or 'unknown'}{loc} · {j.source}")
        print(f"     {j.url}")
        if s.rationale:
            print(f"     ↳ {s.rationale}")
        print()
    if shown == 0:
        print(f"_(no results ≥{min_score})_\n")


async def _amain(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    if profile is None:
        print(
            f"Profile '{args.profile}' not found. "
            f"Place a profile.yaml at ~/.bassito/profiles/{args.profile}/ "
            f"or run the bot's /find_boost_profile command.",
            file=sys.stderr,
        )
        return 2

    if args.source:
        profile.sources_enabled = list(args.source)

    if args.top_only:
        top = await top_for_profile(profile.name, min_score=args.min_score, limit=args.top)

        class _R:  # tiny shim so _print_result is happy
            pass

        r = _R()
        r.profile = profile.name
        r.scraped = r.new = r.scored = 0
        r.top = top
        _print_result(r, min_score=args.min_score)
        return 0

    async def progress(msg: str) -> None:
        print(msg)

    runner = JobSearchRunner(progress=progress)
    result = await runner.run(profile, top_n=args.top, min_score=args.min_score)
    _print_result(result, min_score=args.min_score)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bassito_jobs",
        description="Run a one-shot AI job search against a saved profile.",
    )
    parser.add_argument("profile", help="Profile name (~/.bassito/profiles/<name>/profile.yaml)")
    parser.add_argument("--top", type=int, default=10, help="How many jobs to show")
    parser.add_argument("--min-score", type=int, default=0, help="Hide jobs below this score (0-100)")
    parser.add_argument(
        "--source", action="append",
        help="Restrict to a single source (repeatable). e.g. --source alljobs --source flex",
    )
    parser.add_argument(
        "--top-only", action="store_true",
        help="Skip scraping, just print already-stored top jobs for this profile.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
