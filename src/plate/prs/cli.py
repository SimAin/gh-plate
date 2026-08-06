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

from plate.core import config, gh
from plate.core.gh import PlateError

from . import github, render
from .model import group_by_repo, normalize_rows, summary_counts

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
            "Flag a PR as stale when nobody (human) has touched it in this "
            f"many days. Defaults to {DEFAULT_STALE_DAYS}."
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
        help="Status table for open GitHub pull requests in a repository, or "
        "across every repository of an owner with --owner.",
        description="Status table for open GitHub pull requests in a repository, "
        "or across every repository of an owner with --owner.",
    )
    _add_prs_flags(prs)


def _use_color(args: argparse.Namespace) -> bool:
    return args.color == "always" or (
        args.color == "auto" and sys.stdout.isatty()
    )


def run(args: argparse.Namespace) -> int:
    if args.mine and not args.owner:
        raise PlateError(
            "--mine only applies with --owner. The repo view already shows every "
            "open PR grouped into yours / to review / the rest, so --mine on its "
            "own would do nothing."
        )

    if args.owner:
        # The owner view is not tied to a checkout, so it must not require a git
        # repo — never call gh.current_repo() on this path (#54).
        return _run_owner(args)

    repo = args.repo or gh.current_repo()
    return _run_repo(args, repo)


def _run_repo(args: argparse.Namespace, repo: str) -> int:
    login, prs = github.fetch_prs_and_viewer(repo, args.limit)

    if not prs:
        print(f"No open PRs found for {repo}.")
        return 0

    rows = normalize_rows(
        prs, login, now=datetime.now(UTC), stale_days=args.stale_days, repo=repo
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


def _run_owner(args: argparse.Namespace) -> int:
    # Parallel to plate.issues.cli._run_owner (not imported — the domains stay
    # independent, see DECISIONS.md D8): resolve any alias, resolve the owner
    # type (which doubles as validation), fetch, group by repo, render.
    cfg = config.load_config()

    login = gh.current_login()
    if login is None:
        raise PlateError(
            "Could not determine your GitHub login (is `gh` authenticated?).\n"
            "The owner PR view groups into yours / to review and cannot run "
            "without it."
        )

    resolved = cfg.resolve_owner(args.owner)
    # Show the alias mapping only when one actually fired (the resolver folds
    # case, so compare after resolution); a literal owner shows just its name.
    alias_fired = resolved != args.owner
    display = f"{args.owner} → {resolved}" if alias_fired else args.owner

    try:
        owner_type = gh.resolve_owner_type(resolved)
    except PlateError as exc:
        # An unknown alias falls through resolve_owner as a literal, so a typo'd
        # alias surfaces here as an unknown owner. If aliases are configured,
        # list them so the user can spot the one they meant.
        if cfg.owners:
            aliases = ", ".join(
                f"{alias} → {owner}" for alias, owner in cfg.owners.items()
            )
            raise PlateError(f"{exc}\nConfigured aliases: {aliases}") from exc
        raise

    prs, total = github.fetch_owner_prs(
        resolved, owner_type, login, args.limit, mine=args.mine
    )
    if not prs:
        if args.mine:
            print(f"No open PRs authored by you for {display}.")
        else:
            print(f"No open PRs found for {display}.")
        return 0

    rows = normalize_rows(
        prs, login, now=datetime.now(UTC), stale_days=args.stale_days
    )
    sections = group_by_repo(rows)

    if args.format == "markdown":
        if alias_fired:
            print(f"*{display}*")
            print()
        print(render.owner_markdown(sections))
    else:
        use_color = _use_color(args)
        if args.show_key:
            print(render.owner_key(use_color))
            print()
        if alias_fired:
            print(render.dim(display, use_color))
        print(render.summary_line(summary_counts(rows)))
        print()
        print(render.owner_table(sections, use_color, use_links=sys.stdout.isatty()))

    if len(prs) < total:
        if len(prs) == args.limit:
            print(
                f"\nNote: showing {len(prs)} of {total} open PRs for "
                f"{display} (--limit {args.limit}).",
                file=sys.stderr,
            )
        else:
            print(
                "\nNote: GitHub search returns at most 1000 results per query; "
                f"showing {len(prs)} of {total} open PRs for {display}. "
                "Use --mine or --repo to narrow.",
                file=sys.stderr,
            )
    return 0
