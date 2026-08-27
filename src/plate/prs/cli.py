"""The ``prs`` subcommand: a status table for open GitHub pull requests in a
repository.

Thin wiring layer: parse the ``prs`` flags, ask :mod:`plate.prs.github` for
data, hand it to :mod:`plate.prs.model` to normalize and :mod:`plate.prs.render`
to format. Shared I/O (repo resolution) comes from :mod:`plate.core.gh`; the
JSON config from :mod:`plate.core.config`. All environment failures arrive as
:class:`~plate.core.gh.PlateError` and :func:`plate.cli.main` turns them into a
clean stderr message with a non-zero exit.

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

from plate.core import config, flags, gh, owner
from plate.core.gh import PlateError
from plate.core.render import color_enabled

from . import github, render
from .model import group_by_repo, normalize_rows, summary_counts

DEFAULT_LIMIT = 500
DEFAULT_STALE_DAYS = 14


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got '{value}'")
    return parsed


def _add_prs_flags(parser: argparse.ArgumentParser) -> None:
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--repo",
        help="GitHub repository as OWNER/REPO. Defaults to the current git repo.",
    )
    scope.add_argument(
        "--owner",
        help="Show open PRs across every repository of a GitHub organization "
        "or user account, grouped by repo. Accepts a configured alias (see "
        "README).",
    )
    parser.add_argument(
        "--mine",
        action="store_true",
        help="With --owner, narrow to PRs you authored.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=DEFAULT_LIMIT,
        help=f"Maximum open PRs to fetch. Defaults to {DEFAULT_LIMIT}.",
    )
    parser.add_argument(
        "--stale-days",
        type=_positive_int,
        default=DEFAULT_STALE_DAYS,
        help=(
            "Flag a PR as stale when nobody (human) has touched it in this "
            f"many days. Defaults to {DEFAULT_STALE_DAYS}."
        ),
    )
    parser.add_argument(
        "--timeline",
        action="store_true",
        help="Show a per-PR activity strip of the last 28 days under each "
        "row (repo view, terminal format only; ignored with --format "
        "markdown).",
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
        help="Status table for open GitHub pull requests in a repository, or "
        "across every repository of an owner with --owner.",
        description="Status table for open GitHub pull requests in a repository, "
        "or across every repository of an owner with --owner.",
        parents=[flags.output(), flags.config()],
    )
    _add_prs_flags(prs)


def run(args: argparse.Namespace) -> int:
    if args.config_path:
        print(args.config or config.config_path())
        return 0

    if args.mine and not args.owner:
        raise PlateError(
            "--mine only applies with --owner. The repo view already shows every "
            "open PR grouped into yours / to review / the rest, so --mine on its "
            "own would do nothing."
        )
    if args.timeline and args.owner:
        raise PlateError(
            "--timeline is only available in the repo view: two lines per PR "
            "is too heavy for an owner-wide table."
        )

    if args.owner:
        # The owner view is not tied to a checkout, so it must not require a git
        # repo — never call gh.current_repo() on this path (#54).
        return _run_owner(args)

    repo = args.repo or gh.current_repo()
    return _run_repo(args, repo)


def _run_repo(args: argparse.Namespace, repo: str) -> int:
    # The strip is terminal-only, so markdown never pays for the events fetch.
    timeline = args.timeline and args.format == "terminal"
    login, prs = github.fetch_prs_and_viewer(repo, args.limit, timeline=timeline)

    if not prs:
        print(f"No open PRs found for {repo}.")
        return 0

    rows = normalize_rows(
        prs, login, now=datetime.now(UTC), stale_days=args.stale_days, repo=repo
    )

    if args.format == "markdown":
        print(render.markdown_table(rows))
    else:
        use_color = color_enabled(args.color)
        if args.show_key:
            print(render.symbol_key(use_color, show_timeline=timeline))
            print()
        print(render.summary_line(summary_counts(rows)))
        print()
        # Hyperlinks are orthogonal to colour: emit them for interactive
        # terminals, never into pipes or files.
        print(
            render.terminal_table(
                rows,
                use_color,
                use_links=sys.stdout.isatty(),
                show_timeline=timeline,
            )
        )

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


def _run_owner(args: argparse.Namespace) -> int:
    cfg = config.load_config(args.config)

    login = gh.current_login()
    if login is None:
        raise PlateError(
            "Could not determine your GitHub login (is `gh` authenticated?).\n"
            "The owner PR view groups into yours / to review and cannot run "
            "without it."
        )

    target = owner.resolve_owner(args.owner, cfg)
    display = target.display

    prs, total = github.fetch_owner_prs(
        target.name, target.owner_type, login, args.limit, mine=args.mine
    )
    if not prs:
        if args.mine:
            print(f"No open PRs authored by you for {display}.")
        else:
            print(f"No open PRs found for {display}.")
        return 0

    rows = normalize_rows(prs, login, now=datetime.now(UTC), stale_days=args.stale_days)
    sections = group_by_repo(rows)

    if args.format == "markdown":
        if target.alias_fired:
            print(f"*{display}*")
            print()
        print(render.owner_markdown(sections))
    else:
        use_color = color_enabled(args.color)
        if args.show_key:
            print(render.owner_key(use_color))
            print()
        if target.alias_fired:
            print(render.dim(display, use_color))
        print(render.summary_line(summary_counts(rows)))
        print()
        print(render.owner_table(sections, use_color, use_links=sys.stdout.isatty()))

    note = owner.listing_truncation_note(
        "open PRs", display, len(prs), total, args.limit
    )
    if note:
        print(note, file=sys.stderr)
    return 0
