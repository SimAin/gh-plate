"""issue-check — a status table for the open GitHub issues assigned to you.

Thin wiring layer: parse args, ask :mod:`issue_check.github` for data, hand it
to :mod:`issue_check.model` to normalize and :mod:`issue_check.render` to format.
All environment failures arrive as :class:`~issue_check.github.IssueCheckError`
and are turned into a clean stderr message with a non-zero exit here.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from . import config, github, render
from .github import IssueCheckError
from .model import build_forest, build_index

DEFAULT_LIMIT = 500
DEFAULT_STALE_DAYS = 14


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="issue-check",
        description="Status table for open GitHub issues assigned to you.",
    )
    parser.add_argument(
        "--repo",
        help="GitHub repository as OWNER/REPO. Defaults to the current git repo.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum issues to fetch. Defaults to {DEFAULT_LIMIT}.",
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
        type=int,
        default=DEFAULT_STALE_DAYS,
        help=(
            "Flag an issue stale when not updated in this many days. "
            f"Defaults to {DEFAULT_STALE_DAYS}."
        ),
    )
    parser.add_argument(
        "--show-key",
        action="store_true",
        help="Print a key explaining the symbols above the table.",
    )
    parser.add_argument(
        "--config",
        help="Path to a JSON config file. Defaults to $ISSUE_CHECK_CONFIG or "
        "~/.config/issue-check/config.json.",
    )
    parser.add_argument(
        "--config-path",
        action="store_true",
        help="Print the resolved config file location and exit.",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    if args.config_path:
        print(args.config or config.config_path())
        return 0

    cfg = config.load_config(args.config)
    repo = args.repo or github.current_repo()

    login = github.current_login()
    if login is None:
        raise IssueCheckError(
            "Could not determine your GitHub login (is `gh` authenticated?).\n"
            "issue-check groups by assignee and cannot run without it."
        )

    issues, total = github.fetch_assigned_issues(repo, login, args.limit)
    if not issues:
        print(f"No open issues assigned to you in {repo}.")
        return 0

    index = build_index(
        issues, now=datetime.now(UTC), stale_days=args.stale_days
    )
    forest = build_forest(index)

    if args.format == "markdown":
        print(render.markdown_tree(forest, cfg.style_for))
    else:
        use_color = args.color == "always" or (
            args.color == "auto" and sys.stdout.isatty()
        )
        if args.show_key:
            print(render.symbol_key(use_color))
            print()
        print(render.terminal_tree(forest, use_color, cfg.style_for))

    if len(issues) == args.limit and total > args.limit:
        print(f"\nNote: showing {args.limit} of {total} assigned issues.")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except IssueCheckError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
