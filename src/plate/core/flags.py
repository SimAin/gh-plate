"""The cross-cutting CLI flags, declared once.

``--format`` and ``--color`` are identical in every view; ``--config`` and
``--config-path`` belong to the views that read the JSON config. Each is an
``add_help=False`` parent parser a subcommand mounts via ``add_parser(...,
parents=[...])``, so a flag and its help text have exactly one home and
``retro --help`` stays truthful about the config flags it doesn't take.
"""

from __future__ import annotations

import argparse


def output() -> argparse.ArgumentParser:
    """Parent parser carrying ``--format`` and ``--color``."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--format",
        choices=("terminal", "markdown", "json"),
        default="terminal",
        help="Output format. Defaults to terminal. json is a stable envelope "
        "for scripts (see README).",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="Colour terminal output. Defaults to auto, which honours NO_COLOR "
        "and FORCE_COLOR, skips colour under TERM=dumb, and otherwise colours "
        "only a terminal.",
    )
    return parser


def config() -> argparse.ArgumentParser:
    """Parent parser carrying ``--config`` and ``--config-path``."""
    parser = argparse.ArgumentParser(add_help=False)
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
    return parser
