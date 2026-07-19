"""The ``prs`` subcommand: a status table for open GitHub pull requests in a
repository.

Thin wiring layer: parse the ``prs`` flags, ask :mod:`plate.prs.github` for
data, hand it to :mod:`plate.prs.model` to normalize and :mod:`plate.prs.render`
to format. Shared I/O (repo resolution) comes from :mod:`plate.core.gh`; all
environment failures arrive as :class:`~plate.core.gh.PlateError` and
:func:`plate.cli.main` turns them into a clean stderr message with a non-zero
exit.

Exposes :func:`add_parser` (registers the ``prs`` subparser) and :func:`run`
(the subcommand's entry point) for :mod:`plate.cli` to wire up.

Ported from the standalone ``gh-pr-status`` tool's ``main()`` during the
absorption epic (#50), at the behavioural-parity bar (#53): same flags,
grouping, summary line, and notes.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from plate.core import gh

from . import github, render
from .model import normalize_rows, summary_counts

DEFAULT_LIMIT = 500
DEFAULT_STALE_DAYS = 14


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(
            f"must be a positive integer, got '{value}'"
        )
    return parsed


def _add_prs_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo",
        help="GitHub repository as OWNER/REPO. Defaults to the current git repo.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=DEFAULT_LIMIT,
        help=f"Maximum open PRs to fetch. Defaults to {DEFAULT_LIMIT}.",
    )
    parser.add_argument(
        "--format",
        choices=("terminal", "markdown"),
        default="terminal",
        help="Output format. Defaults to terminal.",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="Colour terminal output. Defaults to auto.",
    )
    parser.add_argument(
        "--stale-days",
        type=_positive_int,
        default=DEFAULT_STALE_DAYS,
        help=(
            "Flag a PR as stale when not updated in this many days. "
            f"Defaults to {DEFAULT_STALE_DAYS}."
        ),
    )
    parser.add_argument(
        "--show-key",
        action="store_true",
        help="Print a key explaining the symbols above the table.",
    )


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``prs`` subparser (with its flags) on ``subparsers``."""
    prs = subparsers.add_parser(
        "prs",
        help="Status table for open GitHub pull requests in a repository.",
        description="Status table for open GitHub pull requests in a repository.",
    )
    _add_prs_flags(prs)


def _use_color(args: argparse.Namespace) -> bool:
    return args.color == "always" or (
        args.color == "auto" and sys.stdout.isatty()
    )


def run(args: argparse.Namespace) -> int:
    repo = args.repo or gh.current_repo()
    login, prs = github.fetch_prs_and_viewer(repo, args.limit)

    if not prs:
        print(f"No open PRs found for {repo}.")
        return 0

    rows = normalize_rows(
        prs, login, now=datetime.now(UTC), stale_days=args.stale_days
    )

    if args.format == "markdown":
        print(render.markdown_table(rows))
    else:
        use_color = _use_color(args)
        if args.show_key:
            print(render.symbol_key(use_color))
            print()
        print(render.summary_line(summary_counts(rows)))
        print()
        # Hyperlinks are orthogonal to colour: emit them for interactive
        # terminals, never into pipes or files.
        print(render.terminal_table(rows, use_color, use_links=sys.stdout.isatty()))

    if len(prs) == args.limit:
        print(
            f"\nNote: fetched {args.limit} open PRs; there may be more not shown.",
            file=sys.stderr,
        )
    if login is None:
        print(
            "\nNote: current GitHub login could not be determined, so PRs could "
            "not be grouped into yours / to review.",
            file=sys.stderr,
        )

    return 0
