"""plate — a status table for GitHub work.

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
from .model import build_forest, build_index, build_sprint_view, group_by_repo

DEFAULT_LIMIT = 500
DEFAULT_STALE_DAYS = 14


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(
            f"must be a positive integer, got '{value}'"
        )
    return parsed


def _add_version(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )


def _add_issues_flags(parser: argparse.ArgumentParser) -> None:
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--repo",
        help="GitHub repository as OWNER/REPO. Defaults to the current git repo.",
    )
    scope.add_argument(
        "--owner",
        help="Show open issues across every repository of a GitHub organization "
        "or user account. Accepts a configured alias (see README).",
    )
    parser.add_argument(
        "--mine",
        action="store_true",
        help="With --owner, narrow to issues assigned to you.",
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plate",
        description="Status table for what's on your plate — open GitHub work. "
        "Run `plate issues` for the issues you're assigned, or across every "
        "repository of an owner with --owner.",
    )
    _add_version(parser)
    subparsers = parser.add_subparsers(dest="command")
    issues = subparsers.add_parser(
        "issues",
        help="Status table for open GitHub issues assigned to you, or "
        "across every repository of an owner with --owner.",
        description="Status table for open GitHub issues assigned to you, or "
        "across every repository of an owner with --owner.",
    )
    _add_issues_flags(issues)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def _use_color(args: argparse.Namespace) -> bool:
    return args.color == "always" or (
        args.color == "auto" and sys.stdout.isatty()
    )


def run(args: argparse.Namespace) -> int:
    if args.config_path:
        print(args.config or config.config_path())
        return 0

    if args.mine and not args.owner:
        raise IssueCheckError(
            "--mine only applies with --owner. The default view already shows "
            "only your assigned issues, so --mine on its own would do nothing."
        )
    if args.sprint and args.owner:
        raise IssueCheckError(
            "--sprint is per-repo and cannot be combined with --owner. Run "
            "--sprint on the current repo (or with --repo OWNER/REPO)."
        )

    cfg = config.load_config(args.config)

    login = github.current_login()
    if login is None:
        raise IssueCheckError(
            "Could not determine your GitHub login (is `gh` authenticated?).\n"
            "issue-check groups by assignee and cannot run without it."
        )

    if args.owner:
        # The owner view is not tied to a checkout, so it must not require a git
        # repo — never call github.current_repo() on this path (#43).
        return _run_owner(args, cfg, login)

    repo = args.repo or github.current_repo()
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
        issues, now=datetime.now(UTC), stale_days=args.stale_days, repo=repo
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


def _run_owner(args: argparse.Namespace, cfg: config.Config, login: str) -> int:
    resolved = cfg.resolve_owner(args.owner)
    # Show the alias mapping only when one actually fired (the resolver folds
    # case, so compare after resolution); a literal owner shows just its name.
    alias_fired = resolved != args.owner
    display = f"{args.owner} → {resolved}" if alias_fired else args.owner

    try:
        owner_type = github.resolve_owner_type(resolved)
    except IssueCheckError as exc:
        # An unknown alias falls through resolve_owner as a literal, so a typo'd
        # alias surfaces here as an unknown owner. If aliases are configured,
        # list them so the user can spot the one they meant.
        if cfg.owners:
            aliases = ", ".join(
                f"{alias} → {owner}" for alias, owner in cfg.owners.items()
            )
            raise IssueCheckError(
                f"{exc}\nConfigured aliases: {aliases}"
            ) from exc
        raise

    issues, total = github.fetch_owner_issues(
        resolved, owner_type, login, args.limit, mine=args.mine
    )
    if not issues:
        if args.mine:
            print(f"No open issues assigned to you for {display}.")
        else:
            print(f"No open issues found for {display}.")
        return 0

    # repo=resolved is only the fallback for a payload missing
    # repository.nameWithOwner; the owner query always carries it, so this is
    # inert here — it just keeps build_index's contract satisfied.
    index = build_index(
        issues,
        now=datetime.now(UTC),
        stale_days=args.stale_days,
        repo=resolved,
        login=login,
    )
    sections = group_by_repo(index)

    if args.format == "markdown":
        if alias_fired:
            print(f"*{display}*")
            print()
        print(render.owner_markdown(sections, cfg.style_for))
    else:
        use_color = _use_color(args)
        if args.show_key:
            print(render.owner_key(use_color))
            print()
        if alias_fired:
            print(render.dim(display, use_color))
        print(render.owner_tree(sections, use_color, cfg.style_for))

    if len(issues) < total:
        if len(issues) == args.limit:
            print(
                f"\nNote: showing {len(issues)} of {total} open issues for "
                f"{display} (--limit {args.limit}).",
                file=sys.stderr,
            )
        else:
            print(
                "\nNote: GitHub search returns at most 1000 results per query; "
                f"showing {len(issues)} of {total} open issues for {display}. "
                "Use --mine or --repo to narrow.",
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

    # One cheap fields query first: validate the configured field names — and
    # each configured statusOrder entry against the status field's real
    # options — against the board's real fields, so a misconfiguration fails
    # fast with an actionable error instead of silently dumping the whole
    # board (#2, #4) or silently degrading the active-first sort (#7).
    fields = github.fetch_project_fields(
        project.owner, project.owner_type, project.number
    )
    github.validate_board_fields(
        fields, project.sprint_field, project.status_field, project.status_order
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
            print(render.sprint_key(use_color))
            print()
        print(render.sprint_table(view, use_color, cfg.style_for))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # Bare `plate`: show the top-level help and point at the issues view.
        parser.print_help()
        print("\nHint: run `plate issues` to see the issues assigned to you.")
        return 0
    try:
        return run(args)
    except IssueCheckError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
