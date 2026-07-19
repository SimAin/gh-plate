"""plate — a status table for GitHub work.

Top-level wiring only: build the argument parser, dispatch to a subcommand,
and turn a :class:`~plate.core.gh.PlateError` into a clean stderr message with
a non-zero exit. This module knows nothing about issues, sprints, owners, or
PRs — that logic lives in each domain's own ``cli`` module (see
:mod:`plate.issues.cli`, :mod:`plate.prs.cli`), which this module wires up via
:func:`add_parser` and :func:`run`. This is the one place both domains may be
named.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from . import __version__
from .core.gh import PlateError
from .issues import cli as issues_cli
from .prs import cli as prs_cli

_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "issues": issues_cli.run,
    "prs": prs_cli.run,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plate",
        description="Status table for what's on your plate — open GitHub work. "
        "Run `plate issues` for the issues you're assigned, or across every "
        "repository of an owner with --owner, or `plate prs` for open pull "
        "requests in a repository.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")
    issues_cli.add_parser(subparsers)
    prs_cli.add_parser(subparsers)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # Bare `plate`: show the top-level help and point at both views.
        parser.print_help()
        print(
            "\nHint: run `plate issues` to see the issues assigned to you, or "
            "`plate prs` to see open pull requests."
        )
        return 0
    try:
        return _COMMANDS[args.command](args)
    except PlateError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
