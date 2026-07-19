"""Domain-agnostic presentation primitives shared by every plate view: ANSI
colour/style helpers, width-aware cell formatting, hyperlinks. Pure — no I/O.

Every domain package's renderer (see :mod:`plate.issues.render`, and later a
``plate.prs.render``) builds its tables and trees on top of these; nothing
here knows about issues, sprints, PRs, or any other domain concept.
"""

from __future__ import annotations

import re
import shutil

# xterm-256 soft tints — muted hues so a coloured glyph reads as signal, not noise.
SOFT_GREEN = "\033[38;5;151m"
SOFT_ROSE = "\033[38;5;210m"
SOFT_GOLD = "\033[38;5;222m"
# A soft, non-health blue — the one tint reserved for signal that isn't health
# (e.g. the PR views' Release-PR marker). Kept in the shared palette beside its
# siblings; where it's *used* is a domain decision, the hue itself is not.
SOFT_BLUE = "\033[38;5;110m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
ANSI_RE = re.compile(r"\033\[[0-9;]*m")
# OSC-8 hyperlink sequences: ESC ] 8 ; params ; URI  ST  (ST = ESC \ or BEL).
# Stripped for width math so a linked #num still measures as its visible text.
_OSC8_RE = re.compile(r"\033\]8;[^\033\007]*(?:\033\\|\007)")


def visible_text(value: str) -> str:
    return ANSI_RE.sub("", _OSC8_RE.sub("", value))


def visible_length(value: str) -> int:
    return len(visible_text(value))


def truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    if max_length <= 1:
        return value[:max_length]
    return value[: max_length - 1] + "…"


def colorize(value: str, color: str, enabled: bool) -> str:
    if not enabled or not value:
        return value
    return f"{color}{value}{RESET}"


def bold(value: str, enabled: bool) -> str:
    return colorize(value, BOLD, enabled)


def dim(value: str, enabled: bool) -> str:
    return colorize(value, DIM, enabled)


def hyperlink(text: str, url: str, enabled: bool) -> str:
    """Wrap ``text`` in an OSC-8 terminal hyperlink to ``url``.

    A no-op when disabled or there's no url, so ``--color never`` and piped
    output stay plain. Terminals without OSC-8 support silently ignore the
    escapes, showing the bare ``text``.
    """
    if not enabled or not url:
        return text
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def format_cell(value: str, width: int, align: str = "left") -> str:
    """Pad (or hard-truncate) a possibly-coloured cell to ``width`` columns.

    Truncation here strips ANSI as a last resort; callers that colour a cell
    should size its visible content to ``width`` first (see ``_tree_cell``).
    """
    if visible_length(value) > width:
        value = truncate(visible_text(value), width)
    padding = " " * max(0, width - visible_length(value))
    return padding + value if align == "right" else value + padding


def format_age(age_days: int | None) -> str:
    if age_days is None:
        return ""
    if age_days < 14:
        return f"{age_days}d"
    if age_days < 70:
        return f"{age_days // 7}w"
    if age_days < 365:
        return f"{age_days // 30}mo"
    return f"{age_days // 365}y"


def escape_markdown_cell(value: str) -> str:
    return value.replace("|", r"\|")


def terminal_width() -> int:
    return shutil.get_terminal_size(fallback=(120, 24)).columns


def divider(label: str, width: int, use_color: bool) -> str:
    prefix = f"── {label} "
    fill = "─" * max(0, width - len(prefix))
    return dim(prefix + fill, use_color)
