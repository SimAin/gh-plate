"""Presentation: a forest of :class:`~issue_check.model.TreeNode` -> strings.

Pure rendering — takes the forest plus flags, returns a string; no I/O. Follows
the inherited discipline: colour is rationed to *health* (the leading glyph and
a stale Age); everything else is carried by *weight*. Hierarchy is shown by
indentation — a node sits beneath its parent — so no breadcrumb glyph is needed.
Un-owned context ancestors are dimmed whole. See ``spec.md`` / ``MVP.md``.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable

from .model import (
    ACTIVE,
    PR_CLOSED,
    PR_DRAFT,
    PR_MERGED,
    PR_OPEN,
    STALE,
    TreeNode,
    flatten,
    issue_state,
    progress_text,
)

# xterm-256 soft tints — muted hues so a coloured glyph reads as signal, not noise.
SOFT_GREEN = "\033[38;5;151m"
SOFT_ROSE = "\033[38;5;210m"
SOFT_GOLD = "\033[38;5;222m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")
# OSC-8 hyperlink sequences: ESC ] 8 ; params ; URI  ST  (ST = ESC \ or BEL).
# Stripped for width math so a linked #num still measures as its visible text.
_OSC8_RE = re.compile(r"\033\]8;[^\033\007]*(?:\033\\|\007)")

MIN_TREE_WIDTH = 30
MAX_TREE_WIDTH = 90
AGE_W = 4
LABELS_W = 18
PROG_W = 4
CMT_W = 3

STATE_GLYPHS = {STALE: ("•", SOFT_GOLD), ACTIVE: ("✓", SOFT_GREEN)}
STATE_LABELS = {STALE: "stale", ACTIVE: "active"}
CONTEXT_GLYPH = "·"  # an un-owned ancestor, shown only for structure

# "Special" label styles (see config.py). A recognised label is pulled to the
# front of the Labels cell and shown bright in this colour; "hide" drops it.
LABEL_STYLE_COLORS = {"alert": SOFT_ROSE, "warn": SOFT_GOLD, "info": SOFT_GREEN}


def _no_style(_label: str) -> str | None:
    return None

# Linked-PR markers — a second glyph beside the health glyph, each a single
# terminal column (no wide glyphs, so the tree cell stays aligned). The "in
# flight" states share the ⇄ glyph (draft = dimmed, matching how GitHub greys
# drafts); a merged-but-still-open PR is the same glyph tinted green (a nudge to
# close); a closed/abandoned PR is a red ✗. This is a deliberate, intentional
# use of colour beyond health — the linked-PR state is worth a glance of colour.
#   value: (glyph, colour | None, dim?)
PR_GLYPHS = {
    PR_OPEN: ("⇄", None, False),        # open PR — a fix is in flight
    PR_DRAFT: ("⇄", None, True),        # draft PR — work in progress (dimmed)
    PR_MERGED: ("⇄", SOFT_GREEN, False),  # merged, yet the issue is still open
    PR_CLOSED: ("✗", SOFT_ROSE, False),   # closed unmerged — an abandoned attempt
}
PR_LABELS = {
    PR_OPEN: "open PR",
    PR_DRAFT: "draft PR",
    PR_MERGED: "merged (issue still open)",
    PR_CLOSED: "closed/abandoned PR",
}
# Short state words for markdown, where colour is unavailable to disambiguate
# the shared ⇄ glyph.
PR_MD_WORDS = {
    PR_OPEN: "open",
    PR_DRAFT: "draft",
    PR_MERGED: "merged",
    PR_CLOSED: "closed",
}


# --- text / ANSI primitives --------------------------------------------------

def visible_text(value: str) -> str:
    return _ANSI_RE.sub("", _OSC8_RE.sub("", value))


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
    return f"{age_days // 30}mo"


def escape_markdown_cell(value: str) -> str:
    return value.replace("|", r"\|")


def _pack_labels(labels: list[str], width: int) -> str:
    """Greedy whole-label packing with a ``+N`` overflow count (plain text).

    Labels are joined with `` · `` left-to-right while they (plus the eventual
    ``+N`` for what's left) still fit, so the cell reads as an at-a-glance
    context indicator instead of a mid-word-truncated run. A single label that
    alone exceeds the column is the only thing ever ellipsis-truncated — and it
    is unambiguous, since there is nothing else it could be mashed against.
    """
    if not labels:
        return ""
    sep = " · "
    placed: list[str] = []
    for label in labels:
        remaining = len(labels) - (len(placed) + 1)
        suffix = f" +{remaining}" if remaining > 0 else ""
        if len(sep.join([*placed, label]) + suffix) <= width:
            placed.append(label)
        else:
            break
    if placed:
        remaining = len(labels) - len(placed)
        suffix = f" +{remaining}" if remaining > 0 else ""
        return sep.join(placed) + suffix
    # Nothing fit: a single over-long first label. Keep the +N when more labels
    # follow (truncating only this one) so the count is never silently lost.
    remaining = len(labels) - 1
    suffix = f" +{remaining}" if remaining > 0 else ""
    return truncate(labels[0], width - len(suffix)) + suffix


def format_labels(
    labels: list[str],
    width: int,
    use_color: bool = False,
    resolver: Callable[[str], str | None] | None = None,
) -> str:
    """Render the Labels cell: special labels promoted + coloured, rest packed.

    ``resolver(name)`` returns a style (``alert``/``warn``/``info``/``hide``) or
    ``None``. Recognised labels are pulled to the **front**, shown bright in the
    style colour; ``hide`` drops a label; everything else is packed dim with the
    ``+N`` overflow count (see :func:`_pack_labels`). With no resolver this is
    just the plain packed cell, so existing behaviour is unchanged.
    """
    if not labels:
        return ""
    resolve = resolver or _no_style
    sep = " · "
    specials: list[str] = []  # already-coloured chunks, shown bright
    specials_w = 0            # their combined visible width (incl. separators)
    normals: list[str] = []
    for name in labels:
        style = resolve(name)
        if style == "hide":
            continue
        color = LABEL_STYLE_COLORS.get(style) if style else None
        if color:
            specials.append(colorize(name, color, use_color))
            specials_w += (len(sep) if specials_w else 0) + len(name)
        else:
            normals.append(name)

    if specials and normals:
        remaining = width - specials_w - len(sep)
    elif specials:
        remaining = 0
    else:
        remaining = width

    normals_plain = _pack_labels(normals, remaining) if remaining > 0 else ""
    parts = [
        part
        for part in (sep.join(specials), dim(normals_plain, use_color))
        if part
    ]
    return sep.join(parts)


# --- terminal tree -----------------------------------------------------------

def terminal_width() -> int:
    return shutil.get_terminal_size(fallback=(120, 24)).columns


def _tree_width(term: int) -> int:
    fixed = AGE_W + LABELS_W + PROG_W + CMT_W
    separators = 2 * 4  # five columns, four gaps
    return max(MIN_TREE_WIDTH, min(term - fixed - separators, MAX_TREE_WIDTH))


def _divider(label: str, width: int, use_color: bool) -> str:
    prefix = f"── {label} "
    fill = "─" * max(0, width - len(prefix))
    return dim(prefix + fill, use_color)


def _pr_marker(pr_state: str | None, use_color: bool) -> str:
    """A single-column PR marker (or a blank space when nothing is linked)."""
    if not pr_state:
        return " "
    glyph, color, dimmed = PR_GLYPHS[pr_state]
    if color:
        return colorize(glyph, color, use_color)
    return dim(glyph, use_color) if dimmed else glyph


def _tree_cell(node: TreeNode, width: int, use_color: bool) -> str:
    """``<indent><glyph><pr> <#num> <title>`` sized to ``width`` before colour.

    Indentation conveys parenthood; the health glyph carries health for owned
    issues (or a neutral ``·`` for an un-owned context ancestor). A second,
    colourless glyph sits flush against it for the linked-PR state — or a blank
    column when there is none, so numbers and titles stay aligned either way.
    """
    row = node.row
    indent = "  " * node.depth
    num = f"#{row.number}"
    glyph = STATE_GLYPHS[issue_state(row)][0] if row.mine else CONTEXT_GLYPH
    pr = _pr_marker(row.pr_state if row.mine else None, use_color)
    # head: indent + health glyph + space + pr column (1) + space + num + space
    head = len(indent) + len(glyph) + 1 + 1 + 1 + len(num) + 1
    title = truncate(row.title, max(0, width - head))

    if not row.mine:
        # context ancestor: blank health+PR columns keep it aligned with owned rows
        linked = hyperlink(num, row.url, use_color)
        return dim(f"{indent}{glyph}   {linked} {title}", use_color)

    color = STATE_GLYPHS[issue_state(row)][1]
    linked = hyperlink(dim(num, use_color), row.url, use_color)
    return f"{indent}{colorize(glyph, color, use_color)} {pr} {linked} {title}"


def terminal_tree(
    forest: list[TreeNode],
    use_color: bool,
    resolver: Callable[[str], str | None] | None = None,
) -> str:
    tree_w = _tree_width(terminal_width())
    columns = [
        ("Issue", tree_w, "left"),
        ("Age", AGE_W, "right"),
        ("Labels", LABELS_W, "left"),
        ("Prog", PROG_W, "right"),
        ("Cmt", CMT_W, "right"),
    ]
    total = sum(w for _, w, _ in columns) + 2 * (len(columns) - 1)
    header = "  ".join(format_cell(n, w, a) for n, w, a in columns)
    lines = [bold(header, use_color), _divider("yours", total, use_color)]

    for node in flatten(forest):
        row = node.row
        if row.mine:
            age_text = format_age(row.age_days)
            age = (
                colorize(age_text, SOFT_ROSE, use_color)
                if row.is_stale
                else dim(age_text, use_color)
            )
            labels = format_labels(row.labels, LABELS_W, use_color, resolver)
            cmt = dim(str(row.comments_count), use_color)
        else:
            # context ancestor: structure only, no health/labels/comments
            age = labels = cmt = ""
        prog = dim(progress_text(row), use_color)  # parents show their rollup

        cells = [
            (_tree_cell(node, tree_w, use_color), tree_w, "left"),
            (age, AGE_W, "right"),
            (labels, LABELS_W, "left"),
            (prog, PROG_W, "right"),
            (cmt, CMT_W, "right"),
        ]
        lines.append("  ".join(format_cell(v, w, a) for v, w, a in cells))

    return "\n".join(line.rstrip() for line in lines)


# --- markdown tree (nested list — the natural shape for a hierarchy) ---------

def markdown_tree(
    forest: list[TreeNode],
    resolver: Callable[[str], str | None] | None = None,
) -> str:
    resolve = resolver or _no_style
    lines: list[str] = []
    for node in flatten(forest):
        row = node.row
        indent = "  " * node.depth
        link = f"[#{row.number}]({row.url})" if row.url else f"#{row.number}"
        title = row.title

        if not row.mine:
            # un-owned context ancestor, italicised
            rollup = f" · {progress_text(row)}" if row.has_children else ""
            lines.append(f"{indent}- *{link} {title}{rollup}*")
            continue

        glyph = STATE_GLYPHS[issue_state(row)][0]
        meta = []
        if row.pr_state:
            pr_glyph = PR_GLYPHS[row.pr_state][0]
            word = PR_MD_WORDS[row.pr_state]
            ref = f" #{row.pr_number}" if row.pr_number else ""
            meta.append(f"{pr_glyph}{ref} {word}")
        age = format_age(row.age_days)
        if age:
            meta.append(age)
        if row.has_children:
            meta.append(progress_text(row))
        if row.labels:
            # special labels first (bold), ordinary after; "hide" dropped
            specials = [n for n in row.labels if resolve(n) in LABEL_STYLE_COLORS]
            ordinary = [n for n in row.labels if resolve(n) is None]
            shown = [f"**{n}**" for n in specials] + ordinary
            if shown:
                meta.append(", ".join(shown))
        if row.comments_count:
            meta.append(f"{row.comments_count}c")
        suffix = (" · " + " · ".join(meta)) if meta else ""
        lines.append(f"{indent}- {glyph} {link} {title}{suffix}")

    return "\n".join(lines)


# --- key ---------------------------------------------------------------------

def symbol_key(use_color: bool) -> str:
    states = "   ".join(
        f"{colorize(STATE_GLYPHS[s][0], STATE_GLYPHS[s][1], use_color)} "
        f"{STATE_LABELS[s]}"
        for s in (STALE, ACTIVE)
    )
    prs = "   ".join(
        f"{_pr_marker(s, use_color)} {PR_LABELS[s]}"
        for s in (PR_OPEN, PR_DRAFT, PR_MERGED, PR_CLOSED)
    )
    return "\n".join(
        [
            bold("Key", use_color),
            "  State   " + states + f"   {CONTEXT_GLYPH} parent not assigned to you",
            "  PR      " + prs,
            dim(
                "  Indentation = sub-issue of the line above · "
                "Prog = completed/total sub-issues · Age in rose = stale · "
                "PR glyph = a pull request links this issue",
                use_color,
            ),
        ]
    )
