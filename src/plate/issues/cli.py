"""The ``issues`` subcommand: a status table for open GitHub issues assigned to
you, across an owner's repositories, or a repo's current sprint board.

Thin wiring layer: parse the ``issues`` flags, ask :mod:`plate.issues.github`
for data, hand it to :mod:`plate.issues.model` to normalize and
:mod:`plate.issues.render` to format. Shared I/O (repo/owner-type
resolution) comes from :mod:`plate.core.gh`; the JSON config from
:mod:`plate.core.config`. All environment failures arrive as
:class:`~plate.core.gh.PlateError`; :func:`plate.cli.main` turns them into a
clean stderr message with a non-zero exit.

Exposes :func:`add_parser` (registers the ``issues`` subparser) and
:func:`run` (the subcommand's entry point) for :mod:`plate.cli` to wire up.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from plate.core import config, gh
from plate.core.gh import PlateError
from plate.core.render import color_enabled

from . import github, render
from .model import build_forest, build_index, build_sprint_view, group_by_repo

DEFAULT_LIMIT = 500
DEFAULT_STALE_DAYS = 14


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got '{value}'")
    return parsed


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
        help="Colour terminal output. Defaults to auto, which honours NO_COLOR "
        "and FORCE_COLOR and otherwise colours only a terminal.",
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
        help="Path to a JSON config file. Defaults to $PLATE_CONFIG or "
        "~/.config/plate/config.json.",
    )
    parser.add_argument(
        "--config-path",
        action="store_true",
        help="Print the resolved config file location and exit.",
    )


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``issues`` subparser (with its flags) on ``subparsers``."""
    issues = subparsers.add_parser(
        "issues",
        help="Status table for open GitHub issues assigned to you, or "
        "across every repository of an owner with --owner.",
        description="Status table for open GitHub issues assigned to you, or "
        "across every repository of an owner with --owner.",
    )
    _add_issues_flags(issues)


def _require_login(viewer: str | None) -> str:
    """The viewer login carried by a path's own GraphQL fetch, or a clean error.

    Each fetch requests ``viewer { login }`` in its main query, so no separate
    ``gh api user`` round trip is needed; a missing login still fails with the
    actionable message on the paths that group by it.
    """
    if viewer is None:
        raise PlateError(
            "Could not determine your GitHub login (is `gh` authenticated?).\n"
            "plate groups by assignee and cannot run without it."
        )
    return viewer


def run(args: argparse.Namespace) -> int:
    if args.config_path:
        print(args.config or config.config_path())
        return 0

    if args.mine and not args.owner:
        raise PlateError(
            "--mine only applies with --owner. The default view already shows "
            "only your assigned issues, so --mine on its own would do nothing."
        )
    if args.sprint and args.owner:
        raise PlateError(
            "--sprint is per-repo and cannot be combined with --owner. Run "
            "--sprint on the current repo (or with --repo OWNER/REPO)."
        )

    cfg = config.load_config(args.config)

    if args.owner:
        # The owner view is not tied to a checkout, so it must not require a git
        # repo — never call gh.current_repo() on this path (#43).
        return _run_owner(args, cfg)

    repo = args.repo or gh.current_repo()
    if args.sprint:
        return _run_sprint(args, cfg, repo)
    return _run_yours(args, cfg, repo)


def _run_yours(args: argparse.Namespace, cfg: config.Config, repo: str) -> int:
    # assignee:@me filters server-side without a concrete login, and this view
    # groups nothing by it, so the viewer riding along goes unused here.
    issues, total, _viewer = github.fetch_assigned_issues(repo, args.limit)
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
        use_color = color_enabled(args.color)
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


def _run_owner(args: argparse.Namespace, cfg: config.Config) -> int:
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

    issues, total, viewer = github.fetch_owner_issues(
        resolved, owner_type, args.limit, assignee="@me" if args.mine else None
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
        login=_require_login(viewer),
    )
    sections = group_by_repo(index)

    if args.format == "markdown":
        if alias_fired:
            print(f"*{display}*")
            print()
        print(render.owner_markdown(sections, cfg.style_for))
    else:
        use_color = color_enabled(args.color)
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


def _run_sprint(args: argparse.Namespace, cfg: config.Config, repo: str) -> int:
    project = cfg.project_for(repo)
    if project is None:
        raise PlateError(
            f"No sprint board configured for {repo}.\n"
            'Add a "repos" entry mapping it to a GitHub project board in your '
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

    items, viewer = github.fetch_sprint_items(
        project.owner,
        project.owner_type,
        project.number,
        project.sprint_field,
        project.status_field,
    )
    view = build_sprint_view(
        items,
        login=_require_login(viewer),
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
        use_color = color_enabled(args.color)
        if args.show_key:
            print(render.sprint_key(use_color))
            print()
        print(render.sprint_table(view, use_color, cfg.style_for))
    return 0
