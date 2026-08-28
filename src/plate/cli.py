"""plate — a status table for GitHub work.

Top-level wiring only: build the argument parser, load the config once, hand
both to a subcommand, and turn a :class:`~plate.core.gh.PlateError` into a
clean stderr message with a non-zero exit. This module knows nothing about
issues, sprints, owners, PRs, or retros — that logic lives in each domain's own
``cli`` module (see :mod:`plate.issues.cli`, :mod:`plate.prs.cli`,
:mod:`plate.retro.cli`), which this module wires up via :func:`add_parser` and
:func:`run`. This is the one place the domains may be named.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from collections.abc import Callable
from typing import TextIO

from . import __version__
from .core import config
from .core.gh import PlateError
from .core.render import terminal_width
from .issues import cli as issues_cli
from .prs import cli as prs_cli
from .retro import cli as retro_cli

_COMMANDS: dict[str, Callable[[argparse.Namespace, config.Config], int]] = {
    "issues": issues_cli.run,
    "prs": prs_cli.run,
    "retro": retro_cli.run,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plate",
        description="Status table for what's on your plate — open GitHub work. "
        "Run `plate issues` for the issues you're assigned, or across every "
        "repository of an owner with --owner, `plate prs` for open pull "
        "requests in a repository, or `plate retro` for a retrospective of "
        "your own activity.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")
    issues_cli.add_parser(subparsers)
    prs_cli.add_parser(subparsers)
    retro_cli.add_parser(subparsers)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def tolerate_unencodable(stream: TextIO) -> None:
    """Write UTF-8 regardless of locale (a cp1252 redirect would otherwise raise
    UnicodeEncodeError on the glyphs); if that's refused, at least replace them."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (LookupError, ValueError, OSError):
        reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    tolerate_unencodable(sys.stdout)
    tolerate_unencodable(sys.stderr)
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # Bare `plate`: show the top-level help and point at the views.
        parser.print_help()
        hint = (
            "Hint: run `plate issues` to see the issues assigned to you, "
            "`plate prs` to see open pull requests, or `plate retro` for a "
            "retrospective of your own activity."
        )
        print("\n" + textwrap.fill(hint, width=terminal_width() - 2))
        return 0
    if getattr(args, "config_path", False):
        print(args.config or config.config_path())
        return 0
    try:
        # retro declares no --config, so don't read a file it would ignore.
        cfg = (
            config.load_config(args.config)
            if hasattr(args, "config")
            else config.default_config()
        )
        return _COMMANDS[args.command](args, cfg)
    except PlateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Reader (e.g. `| head`) went away. Point stdout at devnull so the
        # interpreter's exit-time flush doesn't raise a second time.
        _silence_stdout()
        return 141


def _silence_stdout() -> None:
    try:
        fd = sys.stdout.fileno()
    except (OSError, ValueError):
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, fd)
    finally:
        os.close(devnull)


if __name__ == "__main__":
    raise SystemExit(main())
