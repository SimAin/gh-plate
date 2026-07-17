"""plate — a status table for GitHub work.

Top-level wiring only: build the argument parser, dispatch to a subcommand,
and turn a :class:`~plate.core.gh.PlateError` into a clean stderr message with
a non-zero exit. This module knows nothing about issues, sprints, owners, or
(later) PRs — that logic lives in each domain's own ``cli`` module (see
:mod:`plate.issues.cli`), which this module wires up via :func:`add_parser`
and :func:`run`.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .core.gh import PlateError
from .issues import cli as issues_cli


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plate",
        description="Status table for what's on your plate — open GitHub work. "
        "Run `plate issues` for the issues you're assigned, or across every "
        "repository of an owner with --owner.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")
    issues_cli.add_parser(subparsers)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # Bare `plate`: show the top-level help and point at the issues view.
        parser.print_help()
        print("\nHint: run `plate issues` to see the issues assigned to you.")
        return 0
    try:
        return issues_cli.run(args)
    except PlateError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
