"""The ``retro`` subcommand: a retrospective of your own GitHub activity.

Thin wiring layer: parse the flags, fetch the activity sources, hand
them to the model to bucket by owner and the renderer to format. Needs no
repo and no checkout — it runs from anywhere ``gh`` is authenticated.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from plate.core import flags, gh
from plate.core.gh import PlateError
from plate.core.render import color_enabled

from . import github, render
from .model import (
    DEFAULT_DAYS,
    MAX_DAYS,
    MIN_DAYS,
    build_sections,
    commits_from_compares,
    coverage_note,
    push_groups,
    truncation_note,
    unexpanded_note,
    window_start,
)


def _days_in_range(value: str) -> int:
    parsed = int(value)
    if not MIN_DAYS <= parsed <= MAX_DAYS:
        raise argparse.ArgumentTypeError(
            f"must be between {MIN_DAYS} and {MAX_DAYS}, got '{value}'"
        )
    return parsed


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``retro`` subparser (with its flags) on ``subparsers``."""
    retro = subparsers.add_parser(
        "retro",
        help="A day-by-day retrospective of your own GitHub activity — "
        "reviews, commits, PRs opened and closed — split by repository owner.",
        description="A day-by-day retrospective of your own GitHub activity "
        "(reviews, commits, PRs opened and closed), private repositories "
        "included, "
        "split into one panel per repository owner so work and personal "
        "activity read separately. Needs no repository checkout.",
        parents=[flags.output()],
    )
    retro.add_argument(
        "--days",
        type=_days_in_range,
        default=DEFAULT_DAYS,
        help=f"Window size in days ({MIN_DAYS}-{MAX_DAYS}). "
        f"Defaults to {DEFAULT_DAYS}.",
    )


def run(args: argparse.Namespace) -> int:
    login = gh.current_login()
    if login is None:
        raise PlateError(
            "Could not determine your GitHub login (is `gh` authenticated?).\n"
            "The retro view reads your own activity and cannot run without it."
        )

    now = datetime.now(UTC)
    since_date = window_start(args.days, now).date().isoformat()
    # The retro is the slowest view — a couple of dozen sequential calls — so
    # the fetches paint a stderr status line; clear it before anything prints.
    try:
        events = github.fetch_events(login)
        groups = push_groups(events, args.days, now)
        compares = github.fetch_compares(
            [(group.repo, group.base, group.head) for group in groups]
        )
        commit_refs, unexpanded = commits_from_compares(groups, compares, login)
        opened_items, opened_total = github.fetch_opened(login, since_date)
        closed_items, closed_total = github.fetch_closed(login, since_date)
    finally:
        gh.progress_clear()
    sections = build_sections(
        commit_refs, opened_items, closed_items, events, args.days, now
    )

    if not sections:
        print(f"No activity found in the last {args.days} days.")
        return 0

    if args.format == "markdown":
        print(render.markdown_table(sections, args.days))
    else:
        print(render.panel(sections, args.days, now, color_enabled(args.color)))

    notes = [
        truncation_note("PRs opened", len(opened_items), opened_total),
        truncation_note("PRs closed", len(closed_items), closed_total),
        coverage_note(events, args.days, now, github.EVENTS_FEED_CAP),
        unexpanded_note(unexpanded),
    ]
    for note in notes:
        if note:
            print(f"\n{note}", file=sys.stderr)
    return 0
