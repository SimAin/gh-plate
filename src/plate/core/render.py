"""Domain-agnostic presentation primitives shared by every plate view: ANSI
colour/style helpers, width-aware cell formatting, hyperlinks, terminal probes.

Every domain package's renderer (see :mod:`plate.issues.render`, and later a
``plate.prs.render``) builds its tables and trees on top of these; nothing
here knows about issues, sprints, PRs, or any other domain concept.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata

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


# Zero-width code points that carry no column despite not being combining marks:
# ZWJ, the text/emoji variation selectors, and the zero-width space.
_ZERO_WIDTH = frozenset(chr(cp) for cp in (0x200D, 0xFE0E, 0xFE0F, 0x200B))


def char_width(ch: str) -> int:
    """Display columns of one character: 0 for combining/zero-width, 2 for East
    Asian wide/fullwidth (CJK and modern emoji), 1 otherwise. Multi-emoji ZWJ
    sequences may still overcount vs some terminals — same limit as wcwidth.
    """
    if unicodedata.combining(ch) or ch in _ZERO_WIDTH:
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def visible_text(value: str) -> str:
    return ANSI_RE.sub("", _OSC8_RE.sub("", value))


def visible_length(value: str) -> int:
    return sum(char_width(ch) for ch in visible_text(value))


def truncate(value: str, max_length: int) -> str:
    """Cut ``value`` to ``max_length`` display columns, marking a cut with ``…``.

    Widths are display columns (CJK/emoji count as two), so both the length test
    and the slice reserve one column for the ellipsis and never split a
    double-width glyph across the budget.
    """
    if visible_length(value) <= max_length:
        return value
    if max_length <= 1:
        return value[:max_length]
    budget = max_length - 1  # reserve one column for the ellipsis
    out, used = "", 0
    for ch in value:
        w = char_width(ch)
        if used + w > budget:
            break
        out, used = out + ch, used + w
    return out + "…"


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


# Well-formed escape sequences: CSI (ESC [ … final), OSC (ESC ] … ST), and the
# two-byte ESC+Fe forms. Removed whole so no `[31m`-style residue is left behind.
_ESCAPE_SEQ_RE = re.compile(
    r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"  # CSI, 7- or 8-bit introducer
    r"|\x1b\][^\x1b\x07]*(?:\x1b\\|\x07)"  # OSC … ST
    r"|\x1b[@-Z\\-_]"  # two-byte ESC Fe
)


def compact_text(value: object) -> str:
    """One clean line from untrusted text (titles, labels, board fields).

    Escape sequences are removed and any remaining control characters (C0,
    DEL, C1) become spaces, so a crafted title can't smuggle terminal escapes
    or OSC-8 links into the output; whitespace then collapses."""
    if not isinstance(value, str):
        return ""
    stripped = _ESCAPE_SEQ_RE.sub("", value)
    plain = "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in stripped)
    return " ".join(plain.split())


def escape_markdown_cell(value: str) -> str:
    return value.replace("|", r"\|")


def terminal_width() -> int:
    return shutil.get_terminal_size(fallback=(120, 24)).columns


def color_enabled(mode: str) -> bool:
    """Resolve a --color mode. An explicit always/never wins; under auto,
    NO_COLOR (non-empty) disables, then FORCE_COLOR decides (``0``/``false``
    off, anything else on), otherwise colour iff stdout is a tty."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    force = os.environ.get("FORCE_COLOR")
    if force is not None:
        return force.lower() not in ("0", "false")
    return sys.stdout.isatty()


def divider(label: str, width: int, use_color: bool) -> str:
    prefix = f"── {label} "
    fill = "─" * max(0, width - len(prefix))
    return dim(prefix + fill, use_color)
