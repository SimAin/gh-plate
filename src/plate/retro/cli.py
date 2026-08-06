"""The ``retro`` subcommand: a retrospective of your own GitHub activity.

Thin wiring layer: parse the flags, fetch the viewer's activity feed, hand
it to the model to bucket and the renderer to format. Needs no repo and no
checkout — it runs from anywhere ``gh`` is authenticated.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from plate.core import gh
from plate.core.gh import PlateError

from . import github, render
from .model import (
    DEFAULT_DAYS,
    MAX_DAYS,
    MIN_DAYS,
    build_channels,
    coverage_note,
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
        "reviews, pushes, PRs opened — across all repositories.",
        description="A day-by-day retrospective of your own GitHub activity "
        "(reviews, pushes, PRs opened) across all repositories, private ones "
        "included. Needs no repository checkout.",
    )
    retro.add_argument(
        "--days",
        type=_days_in_range,
        default=DEFAULT_DAYS,
        help=f"Window size in days ({MIN_DAYS}-{MAX_DAYS}). "
        f"Defaults to {DEFAULT_DAYS}.",
    )
    retro.add_argument(
        "--format",
        choices=("terminal", "markdown"),
        default="terminal",
        help="Output format. Defaults to terminal.",
    )
    retro.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="Colour terminal output. Defaults to auto.",
    )


def run(args: argparse.Namespace) -> int:
    login = gh.current_login()
    if login is None:
        raise PlateError(
            "Could not determine your GitHub login (is `gh` authenticated?).\n"
            "The retro view reads your own activity feed and cannot run "
            "without it."
        )

    now = datetime.now(UTC)
    events = github.fetch_events(login)
    channels = build_channels(events, args.days, now)

    if args.format == "markdown":
        print(render.markdown_table(channels, args.days))
    else:
        use_color = args.color == "always" or (
            args.color == "auto" and sys.stdout.isatty()
        )
        print(render.panel(channels, args.days, now, use_color))

    note = coverage_note(events, args.days, now, github.EVENTS_FEED_CAP)
    if note:
        print(f"\n{note}", file=sys.stderr)
    return 0
