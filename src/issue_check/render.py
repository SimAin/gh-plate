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
    ROW_CONTEXT,
    ROW_MINE,
    ROW_OTHERS,
    ROW_UNASSIGNED,
    STALE,
    SprintRow,
    SprintView,
    TreeNode,
    flatten,
    issue_state,
    progress_text,
    row_class,
    strip_emoji,
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

# Sprint view adds Assignee + Status columns; Labels is narrowed to fit.
ASSIGNEE_W = 12
STATUS_W = 14
SPRINT_LABELS_W = 14

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
    if age_days < 365:
        return f"{age_days // 30}mo"
    return f"{age_days // 365}y"


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
    glyph = STATE_GLYPHS[issue_state(row)][0] if not row.context else CONTEXT_GLYPH
    pr = _pr_marker(row.pr_state if not row.context else None, use_color)
    # head: indent + health glyph + space + pr column (1) + space + num + space
    head = len(indent) + len(glyph) + 1 + 1 + 1 + len(num) + 1
    title = truncate(row.title, max(0, width - head))

    if row.context:
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
        if not row.context:
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

def _markdown_row(
    node: TreeNode,
    resolver: Callable[[str], str | None] | None,
    *,
    assignee_meta: bool,
) -> str:
    """One markdown list line for ``node`` — shared by the yours and owner views.

    ``assignee_meta`` (owner view only) prepends ``@login`` to an ``others`` row's
    meta chain, the "someone else has this" signal that view needs; the yours
    view passes ``False``, since every non-context row there is yours by
    construction and ``unassigned`` rows are the default audience anyway.
    """
    resolve = resolver or _no_style
    row = node.row
    indent = "  " * node.depth
    link = f"[#{row.number}]({row.url})" if row.url else f"#{row.number}"
    title = row.title

    if row.context:
        # un-owned context ancestor, italicised
        rollup = f" · {progress_text(row)}" if row.has_children else ""
        return f"{indent}- *{link} {title}{rollup}*"

    glyph = STATE_GLYPHS[issue_state(row)][0]
    meta = []
    if assignee_meta and row_class(row) == ROW_OTHERS:
        meta.append(f"@{row.assignees[0]}")
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
    return f"{indent}- {glyph} {link} {title}{suffix}"


def markdown_tree(
    forest: list[TreeNode],
    resolver: Callable[[str], str | None] | None = None,
) -> str:
    return "\n".join(
        _markdown_row(node, resolver, assignee_meta=False)
        for node in flatten(forest)
    )


# --- sprint view -------------------------------------------------------------
#
# Same table discipline as the yours-view, but grouped into three assignee
# buckets (yours / others / unassigned) under a sprint-name title, with Assignee
# and Status columns added. ``yours`` rows are full-weight with a health glyph +
# PR marker; ``others``/``unassigned`` are dimmed whole — the weight-is-attention
# axis again. Each bucket is pre-sorted active-first by the model.

def _sprint_issue_width(term: int) -> int:
    fixed = AGE_W + ASSIGNEE_W + STATUS_W + SPRINT_LABELS_W + PROG_W + CMT_W
    separators = 2 * 6  # seven columns, six gaps
    return max(MIN_TREE_WIDTH, min(term - fixed - separators, MAX_TREE_WIDTH))


def _sprint_issue_cell(row: SprintRow, width: int, use_color: bool) -> str:
    """``<glyph><pr> <#num> <title>`` for a sprint row, sized before colour.

    ``yours`` rows lead with a health glyph + PR marker (mirroring the tree
    cell, minus indentation); ``others``/``unassigned`` use a neutral ``·`` and
    are dimmed whole by the caller.
    """
    num = f"#{row.number}"
    if row.is_mine:
        state = STALE if row.is_stale else ACTIVE
        glyph, color = STATE_GLYPHS[state]
        pr = _pr_marker(row.pr_state, use_color)
        head = len(glyph) + 1 + 1 + 1 + len(num) + 1
        title = truncate(row.title, max(0, width - head))
        linked = hyperlink(dim(num, use_color), row.url, use_color)
        return f"{colorize(glyph, color, use_color)} {pr} {linked} {title}"
    head = len(CONTEXT_GLYPH) + 3 + len(num) + 1
    title = truncate(row.title, max(0, width - head))
    linked = hyperlink(num, row.url, use_color)
    return f"{CONTEXT_GLYPH}   {linked} {title}"


def _sprint_columns(issue_w: int) -> list[tuple[str, int, str]]:
    """The sprint table layout — one source for the header and every row.

    ``issue_w`` is computed from the terminal width; the rest are fixed. Both the
    header and :func:`_sprint_row_line` consume this so widths stay in lock-step.
    """
    return [
        ("Issue", issue_w, "left"),
        ("Age", AGE_W, "right"),
        ("Assignee", ASSIGNEE_W, "left"),
        ("Status", STATUS_W, "left"),
        ("Labels", SPRINT_LABELS_W, "left"),
        ("Prog", PROG_W, "right"),
        ("Cmt", CMT_W, "right"),
    ]


def _sprint_row_line(
    row: SprintRow,
    columns: list[tuple[str, int, str]],
    use_color: bool,
    resolver: Callable[[str], str | None] | None,
) -> str:
    """One sprint row, laid out against ``columns`` (see :func:`_sprint_columns`).

    ``yours`` rows colour the Age (rose when stale) and promote special labels;
    ``others``/``unassigned`` carry the same data plain, then dim the whole line.
    """
    issue = _sprint_issue_cell(row, columns[0][1], use_color)
    age_text = format_age(row.age_days)
    assignee = row.assignees[0] if row.assignees else "—"
    status = strip_emoji(row.status or "")
    prog = f"{row.sub_completed}/{row.sub_total}" if row.has_children else ""
    cmt = str(row.comments_count)

    if row.is_mine:
        age = (
            colorize(age_text, SOFT_ROSE, use_color)
            if row.is_stale
            else dim(age_text, use_color)
        )
        labels = format_labels(row.labels, SPRINT_LABELS_W, use_color, resolver)
        values = [issue, age, assignee, status, labels,
                  dim(prog, use_color), dim(cmt, use_color)]
        return "  ".join(
            format_cell(v, w, a)
            for v, (_, w, a) in zip(values, columns, strict=True)
        )

    # others / unassigned: same data, plain, then dimmed whole. The resolver's
    # "hide" is still honoured (a promise applied everywhere); promotion/colour
    # is not, so these rows stay uniformly dim.
    resolve = resolver or _no_style
    visible_labels = [name for name in row.labels if resolve(name) != "hide"]
    values = [issue, age_text, assignee, status,
              _pack_labels(visible_labels, SPRINT_LABELS_W), prog, cmt]
    line = "  ".join(
        format_cell(v, w, a)
        for v, (_, w, a) in zip(values, columns, strict=True)
    )
    return dim(line, use_color)


def sprint_table(
    view: SprintView,
    use_color: bool,
    resolver: Callable[[str], str | None] | None = None,
) -> str:
    columns = _sprint_columns(_sprint_issue_width(terminal_width()))
    total = sum(w for _, w, _ in columns) + 2 * (len(columns) - 1)
    header = "  ".join(format_cell(n, w, a) for n, w, a in columns)
    title = f"{view.title}  ·  current sprint" if view.title else "current sprint"
    lines = [bold(header, use_color), _divider(title, total, use_color)]

    for name, rows in (
        ("yours", view.yours),
        ("others", view.others),
        ("unassigned", view.unassigned),
    ):
        lines.append(_divider(name, total, use_color))
        for row in rows:
            lines.append(_sprint_row_line(row, columns, use_color, resolver))

    return "\n".join(line.rstrip() for line in lines)


def sprint_markdown(
    view: SprintView,
    resolver: Callable[[str], str | None] | None = None,
) -> str:
    resolve = resolver or _no_style
    title = f"{view.title} · current sprint" if view.title else "current sprint"
    lines: list[str] = [f"## {title}", ""]
    for name, rows in (
        ("yours", view.yours),
        ("others", view.others),
        ("unassigned", view.unassigned),
    ):
        lines.append(f"### {name}")
        if not rows:
            lines.append("- *none*")
        for row in rows:
            link = f"[#{row.number}]({row.url})" if row.url else f"#{row.number}"
            glyph = (
                STATE_GLYPHS[STALE if row.is_stale else ACTIVE][0]
                if row.is_mine
                else CONTEXT_GLYPH
            )
            meta: list[str] = [
                f"@{row.assignees[0]}" if row.assignees else "unassigned"
            ]
            if row.status:
                meta.append(row.status)  # markdown keeps the emoji
            if row.pr_state:
                pr_glyph = PR_GLYPHS[row.pr_state][0]
                word = PR_MD_WORDS[row.pr_state]
                ref = f" #{row.pr_number}" if row.pr_number else ""
                meta.append(f"{pr_glyph}{ref} {word}")
            age = format_age(row.age_days)
            if age:
                meta.append(age)
            if row.has_children:
                meta.append(f"{row.sub_completed}/{row.sub_total}")
            if row.labels:
                specials = [n for n in row.labels if resolve(n) in LABEL_STYLE_COLORS]
                ordinary = [n for n in row.labels if resolve(n) is None]
                shown = [f"**{n}**" for n in specials] + ordinary
                if shown:
                    meta.append(", ".join(shown))
            if row.comments_count:
                meta.append(f"{row.comments_count}c")
            suffix = " · " + " · ".join(meta) if meta else ""
            lines.append(f"- {glyph} {link} {row.title}{suffix}")
        lines.append("")
    return "\n".join(lines).rstrip()


# --- owner-wide view ---------------------------------------------------------
#
# The owner view (issue #43) renders open issues across *all* of an owner's repos,
# one section per repo (most recently active first — see model.group_by_repo).
# Unlike the yours-view tree, every open issue is shown, so rows carry the four
# weights of model.row_class: mine/unassigned full-weight with a health glyph
# (unassigned work in a personal project is still yours to pick up), others dimmed
# whole (weight-is-attention, as in the sprint view), context structure-only.
# Columns mirror the sprint view (Assignee returns as signal), minus Status.

def _owner_issue_width(term: int) -> int:
    fixed = AGE_W + ASSIGNEE_W + SPRINT_LABELS_W + PROG_W + CMT_W
    separators = 2 * 5  # six columns, five gaps
    return max(MIN_TREE_WIDTH, min(term - fixed - separators, MAX_TREE_WIDTH))


def _owner_columns(issue_w: int) -> list[tuple[str, int, str]]:
    """The owner table layout — one source for the header and every row.

    ``issue_w`` is computed from the terminal width; the rest are fixed and
    shared with the sprint view (``ASSIGNEE_W``, ``SPRINT_LABELS_W``). Both the
    header and :func:`_owner_row_line` consume this so widths stay in lock-step.
    """
    return [
        ("Issue", issue_w, "left"),
        ("Age", AGE_W, "right"),
        ("Assignee", ASSIGNEE_W, "left"),
        ("Labels", SPRINT_LABELS_W, "left"),
        ("Prog", PROG_W, "right"),
        ("Cmt", CMT_W, "right"),
    ]


def _owner_tree_cell(node: TreeNode, width: int, use_color: bool) -> str:
    """``<indent><glyph><pr> <#num> <title>`` for an owner row, sized before colour.

    Classified by :func:`model.row_class`: ``mine``/``unassigned`` lead with a
    health glyph + PR marker at full weight (like :func:`_tree_cell`);
    ``others``/``context`` use the neutral ``·`` with blank health+PR columns and
    are dimmed whole by the caller, so numbers and titles stay aligned either way.
    """
    row = node.row
    indent = "  " * node.depth
    num = f"#{row.number}"
    if row_class(row) in (ROW_MINE, ROW_UNASSIGNED):
        state = issue_state(row)
        glyph, color = STATE_GLYPHS[state]
        pr = _pr_marker(row.pr_state, use_color)
        head = len(indent) + len(glyph) + 1 + 1 + 1 + len(num) + 1
        title = truncate(row.title, max(0, width - head))
        linked = hyperlink(dim(num, use_color), row.url, use_color)
        return f"{indent}{colorize(glyph, color, use_color)} {pr} {linked} {title}"
    head = len(indent) + len(CONTEXT_GLYPH) + 3 + len(num) + 1
    title = truncate(row.title, max(0, width - head))
    linked = hyperlink(num, row.url, use_color)
    return f"{indent}{CONTEXT_GLYPH}   {linked} {title}"


def _owner_row_line(
    node: TreeNode,
    columns: list[tuple[str, int, str]],
    use_color: bool,
    resolver: Callable[[str], str | None] | None,
) -> str:
    """One owner row, laid out against ``columns`` (see :func:`_owner_columns`).

    ``mine``/``unassigned`` colour the Age (rose when stale) and promote special
    labels; ``mine`` shows your login dim (low signal — it's you), ``unassigned``
    shows ``—``. ``others`` carry the same data plain (assignee = first login),
    then dim the whole line. ``context`` shows number+title and its rollup only.
    """
    row = node.row
    cls = row_class(row)
    issue = _owner_tree_cell(node, columns[0][1], use_color)
    age_text = format_age(row.age_days)
    prog = progress_text(row)

    if cls in (ROW_MINE, ROW_UNASSIGNED):
        age = (
            colorize(age_text, SOFT_ROSE, use_color)
            if row.is_stale
            else dim(age_text, use_color)
        )
        # mine: assignee is you — low signal, shown dim; unassigned: nobody, "—".
        assignee = (
            dim(row.assignees[0], use_color)
            if cls == ROW_MINE and row.assignees
            else "—"
        )
        labels = format_labels(row.labels, SPRINT_LABELS_W, use_color, resolver)
        values = [issue, age, assignee, labels,
                  dim(prog, use_color), dim(str(row.comments_count), use_color)]
        return "  ".join(
            format_cell(v, w, a)
            for v, (_, w, a) in zip(values, columns, strict=True)
        )

    if cls == ROW_CONTEXT:
        # structural ancestor: number+title + rollup only, then dimmed whole.
        values = [issue, "", "", "", prog, ""]
        line = "  ".join(
            format_cell(v, w, a)
            for v, (_, w, a) in zip(values, columns, strict=True)
        )
        return dim(line, use_color)

    # others: same data as a full row, plain, then dimmed whole. The resolver's
    # "hide" is still honoured; promotion/colour is not, so the row stays dim.
    resolve = resolver or _no_style
    assignee = row.assignees[0] if row.assignees else "—"
    visible_labels = [name for name in row.labels if resolve(name) != "hide"]
    values = [issue, age_text, assignee,
              _pack_labels(visible_labels, SPRINT_LABELS_W), prog,
              str(row.comments_count)]
    line = "  ".join(
        format_cell(v, w, a)
        for v, (_, w, a) in zip(values, columns, strict=True)
    )
    return dim(line, use_color)


def owner_tree(
    sections: list[tuple[str, list[TreeNode]]],
    use_color: bool,
    resolver: Callable[[str], str | None] | None = None,
) -> str:
    """Terminal owner view: one header, then a divider + rows per repo section.

    ``sections`` is ``model.group_by_repo``'s output (``OWNER/REPO`` -> forest),
    already ordered most-recently-active first; the section order is preserved.
    Each divider counts the section's non-context (real, open) rows.
    """
    columns = _owner_columns(_owner_issue_width(terminal_width()))
    total = sum(w for _, w, _ in columns) + 2 * (len(columns) - 1)
    header = "  ".join(format_cell(n, w, a) for n, w, a in columns)
    lines = [bold(header, use_color)]

    for repo, forest in sections:
        nodes = flatten(forest)
        open_count = sum(1 for n in nodes if not n.row.context)
        lines.append(_divider(f"{repo} · {open_count} open", total, use_color))
        for node in nodes:
            lines.append(_owner_row_line(node, columns, use_color, resolver))

    return "\n".join(line.rstrip() for line in lines)


def owner_markdown(
    sections: list[tuple[str, list[TreeNode]]],
    resolver: Callable[[str], str | None] | None = None,
) -> str:
    """Markdown owner view: a ``## OWNER/REPO`` heading + nested list per section.

    Each section reuses :func:`markdown_tree`'s row format (via
    :func:`_markdown_row`), with ``@login`` added to ``others`` rows so a reader
    can tell whose issue it is; ``unassigned`` rows get no assignee meta.
    """
    blocks: list[str] = []
    for repo, forest in sections:
        lines = [f"## {repo}"]
        lines.extend(
            _markdown_row(node, resolver, assignee_meta=True)
            for node in flatten(forest)
        )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


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


def sprint_key(use_color: bool) -> str:
    """The ``--show-key`` text for ``--sprint`` — distinct from :func:`symbol_key`.

    Sprint rows aren't a parent/child tree: each row is a board item bucketed
    into ``yours`` / ``others`` / ``unassigned`` (see :func:`sprint_table`).
    ``yours`` rows carry a health glyph + PR marker like the yours-view;
    ``others``/``unassigned`` rows use the neutral ``·`` and are dimmed whole,
    so the glyph there means "someone else's or unassigned row", not the
    yours-view's "parent not assigned to you".
    """
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
            "  State   " + states
            + f"   {CONTEXT_GLYPH} someone else's / unassigned row (dimmed)",
            "  PR      " + prs + "   (yours rows only)",
            dim(
                "  Rows are grouped yours -> others -> unassigned · "
                "yours rows are full-weight, others/unassigned are dimmed whole · "
                "Prog = completed/total sub-issues · Age in rose = stale · "
                "Status has emoji stripped in the terminal (kept in markdown)",
                use_color,
            ),
        ]
    )


def owner_key(use_color: bool) -> str:
    """The ``--show-key`` text for the owner-wide view — distinct from the others.

    The owner view (issue #43) shows every open issue across an owner's repos,
    grouped by repository (most recently active first). Both yours *and*
    unassigned rows render full-weight with a health glyph — unassigned work in a
    personal project is still yours to pick up — while the neutral ``·`` does
    double duty: someone else's issue (dimmed whole) or a parent shown only for
    structure.
    """
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
            "  State   " + states
            + f"   {CONTEXT_GLYPH} someone else's issue (dimmed) / "
            "parent shown for structure",
            "  PR      " + prs,
            dim(
                "  Rows are grouped by repository, most recently active repo "
                "first · unassigned issues are full-weight (open work you could "
                "pick up) · Assignee — = unassigned · "
                "Prog = completed/total sub-issues · Age in rose = stale",
                use_color,
            ),
        ]
    )
