"""The ``issues`` subcommand: a status table for open GitHub issues assigned to
you, across an owner's repositories, or a repo's current sprint board.

Thin wiring layer: parse the ``issues`` flags, ask :mod:`plate.issues.github`
for data, hand it to :mod:`plate.issues.model` to normalize and
:mod:`plate.issues.render` to format. Shared I/O (repo/owner-type
resolution) comes from :mod:`plate.core.gh`; the JSON config arrives already
loaded from :func:`plate.cli.main`. All environment failures arrive as
:class:`~plate.core.gh.PlateError`; :func:`plate.cli.main` turns them into a
clean stderr message with a non-zero exit.

Exposes :func:`add_parser` (registers the ``issues`` subparser) and
:func:`run` (the subcommand's entry point) for :mod:`plate.cli` to wire up.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from plate.core import config, flags, gh, jsonout, owner
from plate.core.gh import PlateError
from plate.core.render import color_enabled

from . import github, render
from .model import (
    TreeNode,
    build_forest,
    build_index,
    build_sprint_view,
    flat_rows,
    group_by_repo,
    sprint_rows,
)

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


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``issues`` subparser (with its flags) on ``subparsers``."""
    issues = subparsers.add_parser(
        "issues",
        help="Status table for open GitHub issues assigned to you, or "
        "across every repository of an owner with --owner.",
        description="Status table for open GitHub issues assigned to you, or "
        "across every repository of an owner with --owner.",
        parents=[flags.output(), flags.config()],
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


def run(args: argparse.Namespace, cfg: config.Config) -> int:
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
    # groups nothing by it; the viewer riding along only labels the JSON.
    issues, total, viewer = github.fetch_assigned_issues(repo, args.limit)
    now = datetime.now(UTC)
    notes = []
    if len(issues) == args.limit and total > args.limit:
        notes.append(f"Note: showing {args.limit} of {total} assigned issues.")

    if args.format == "json":
        index = build_index(issues, now=now, stale_days=args.stale_days, repo=repo)
        payload = jsonout.envelope(
            command="issues",
            view="assigned",
            now=now,
            login=viewer,
            repo=repo,
            assignee=viewer,
            stale_days=args.stale_days,
            notes=notes,
            data={"issues": flat_rows(build_forest(index))},
        )
        print(jsonout.dumps(payload))
        return 0

    if not issues:
        print(f"No open issues assigned to you in {repo}.")
        return 0

    index = build_index(issues, now=now, stale_days=args.stale_days, repo=repo)
    forest = build_forest(index)

    if args.format == "markdown":
        print(render.markdown_tree(forest, cfg.style_for))
    else:
        use_color = color_enabled(args.color)
        if args.show_key:
            print(render.symbol_key(use_color))
            print()
        print(
            render.terminal_tree(
                forest, use_color, cfg.style_for, use_links=sys.stdout.isatty()
            )
        )

    for note in notes:
        print(f"\n{note}", file=sys.stderr)
    return 0


def _run_owner(args: argparse.Namespace, cfg: config.Config) -> int:
    target = owner.resolve_owner(args.owner, cfg)
    display = target.display

    issues, total, viewer = github.fetch_owner_issues(
        target.name,
        target.owner_type,
        args.limit,
        assignee="@me" if args.mine else None,
    )
    now = datetime.now(UTC)
    note = owner.listing_truncation_note(
        "open issues", display, len(issues), total, args.limit
    )
    notes = [note] if note else []

    def sections_of() -> list[tuple[str, list[TreeNode]]]:
        # repo=target.name is only the fallback for a payload missing
        # repository.nameWithOwner; the owner query always carries it, so
        # this is inert here — it just keeps build_index's contract satisfied.
        index = build_index(
            issues,
            now=now,
            stale_days=args.stale_days,
            repo=target.name,
            login=_require_login(viewer),
        )
        return group_by_repo(index)

    if args.format == "json":
        # An empty result never needed the login in the other formats; keep
        # JSON no stricter.
        rows = (
            [row for _repo, forest in sections_of() for row in flat_rows(forest)]
            if issues
            else []
        )
        payload = jsonout.envelope(
            command="issues",
            view="owner",
            now=now,
            login=viewer,
            owner=target.name,
            assignee=viewer if args.mine else None,
            stale_days=args.stale_days,
            notes=notes,
            data={"issues": rows},
        )
        print(jsonout.dumps(payload))
        return 0

    if not issues:
        if args.mine:
            print(f"No open issues assigned to you for {display}.")
        else:
            print(f"No open issues found for {display}.")
        return 0

    sections = sections_of()

    if args.format == "markdown":
        if target.alias_fired:
            print(f"*{display}*")
            print()
        print(render.owner_markdown(sections, cfg.style_for))
    else:
        use_color = color_enabled(args.color)
        if args.show_key:
            print(render.owner_key(use_color))
            print()
        if target.alias_fired:
            print(render.dim(display, use_color))
        print(
            render.owner_tree(
                sections, use_color, cfg.style_for, use_links=sys.stdout.isatty()
            )
        )

    for note in notes:
        print(f"\n{note}", file=sys.stderr)
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
    now = datetime.now(UTC)
    login = _require_login(viewer)
    view = build_sprint_view(
        items,
        login=login,
        repo=repo,
        now=now,
        stale_days=args.stale_days,
        status_order=project.status_order,
    )
    if args.format == "json":
        payload = jsonout.envelope(
            command="issues",
            view="sprint",
            now=now,
            login=login,
            repo=repo,
            sprint={"title": view.title},
            stale_days=args.stale_days,
            notes=[],
            data={"issues": sprint_rows(view)},
        )
        print(jsonout.dumps(payload))
        return 0

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
        print(
            render.sprint_table(
                view, use_color, cfg.style_for, use_links=sys.stdout.isatty()
            )
        )
    return 0
