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

from . import __version__, config, github, render
from .github import IssueCheckError
from .model import build_forest, build_index, build_sprint_view

DEFAULT_LIMIT = 500
DEFAULT_STALE_DAYS = 14


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(
            f"must be a positive integer, got '{value}'"
        )
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="issue-check",
        description="Status table for open GitHub issues assigned to you.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--repo",
        help="GitHub repository as OWNER/REPO. Defaults to the current git repo.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
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
        type=_positive_int,
        default=DEFAULT_STALE_DAYS,
        help=(
            "Flag an issue stale when not updated in this many days. "
            f"Defaults to {DEFAULT_STALE_DAYS}."
        ),
    )
    parser.add_argument(
        "--sprint",
        action="store_true",
        help="Show the current sprint from the repo's configured GitHub project "
        "board, grouped yours/others/unassigned, instead of your assigned issues.",
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


def _use_color(args: argparse.Namespace) -> bool:
    return args.color == "always" or (
        args.color == "auto" and sys.stdout.isatty()
    )


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

    if args.sprint:
        return _run_sprint(args, cfg, repo, login)
    return _run_yours(args, cfg, repo, login)


def _run_yours(
    args: argparse.Namespace, cfg: config.Config, repo: str, login: str
) -> int:
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
        use_color = _use_color(args)
        if args.show_key:
            print(render.symbol_key(use_color))
            print()
        print(render.terminal_tree(forest, use_color, cfg.style_for))

    if len(issues) == args.limit and total > args.limit:
        print(
            f"\nNote: showing {args.limit} of {total} assigned issues.",
            file=sys.stderr,
        )
    return 0


def _run_sprint(
    args: argparse.Namespace, cfg: config.Config, repo: str, login: str
) -> int:
    project = cfg.project_for(repo)
    if project is None:
        raise IssueCheckError(
            f"No sprint board configured for {repo}.\n"
            "Add a \"repos\" entry mapping it to a GitHub project board in your "
            f"config ({args.config or config.config_path()}). See the README."
        )

    items = github.fetch_sprint_items(
        project.owner,
        project.owner_type,
        project.number,
        project.sprint_field,
        project.status_field,
    )
    view = build_sprint_view(
        items,
        login=login,
        repo=repo,
        now=datetime.now(UTC),
        stale_days=args.stale_days,
        status_order=project.status_order,
    )
    if view.is_empty:
        if view.title is None:
            print(f"No active sprint for {repo}.")
        else:
            print(f"No issues in the current sprint ({view.title}) for {repo}.")
        return 0

    if args.format == "markdown":
        print(render.sprint_markdown(view, cfg.style_for))
    else:
        use_color = _use_color(args)
        if args.show_key:
            print(render.symbol_key(use_color))
            print()
        print(render.sprint_table(view, use_color, cfg.style_for))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except IssueCheckError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
