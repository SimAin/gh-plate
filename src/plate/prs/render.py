"""Presentation: a list of :class:`~plate.prs.model.PrRow`s -> strings.

Pure rendering — takes the rows plus flags, returns a string; no I/O. Follows
the inherited discipline: colour is rationed to *health* (the leading state
glyph, the CI glyph, a coloured Review word, and a stale Last); group membership
is carried by the labelled dividers, and settled work ("the rest") is dimmed
whole. The one deliberate exception is the soft, non-health blue on a Release
PR — a glance of colour flagging the release train. Direction on the Last
column is carried by weight alone (D11): full weight = the other side moved
last and the days are your lag; dim = you moved last, nothing to chase.

Builds on :mod:`plate.core.render` for the domain-agnostic primitives (ANSI/
width helpers, ``format_cell``, ``format_age``, ``hyperlink``, ``divider``);
everything here is specific to the PR table and its markdown counterpart. This
is the PR-view sibling of :mod:`plate.issues.render`.

Ported from the standalone ``gh-pr-status`` tool during the absorption epic
(#50) at the behavioural-parity bar (#53): same information, grouping, states,
and columns, rebuilt fresh on the shared primitives — spacing and truncation
details may differ, and there are no byte-golden expectations.
"""

from __future__ import annotations

from plate.core.render import DIM as DIM
from plate.core.render import SOFT_BLUE, SOFT_GOLD, SOFT_GREEN, SOFT_ROSE
from plate.core.render import bold as bold
from plate.core.render import colorize as colorize
from plate.core.render import dim as dim
from plate.core.render import divider as divider
from plate.core.render import escape_markdown_cell as escape_markdown_cell
from plate.core.render import format_age as format_age
from plate.core.render import format_cell as format_cell
from plate.core.render import hyperlink as hyperlink
from plate.core.render import terminal_width as terminal_width
from plate.core.render import truncate as truncate

from .model import (
    PrRow,
    PrSummary,
    pr_state,
    sort_group,
    sort_key,
    your_move,
)

# The elastic Title column is clamped between these; every other column is fixed.
MIN_TITLE_WIDTH = 16
MAX_TITLE_WIDTH = 50
COLUMN_GAP = 2

GROUP_LABELS = {0: "yours", 1: "to review", 2: "the rest"}

# A PR's single derived state (see model.pr_state), in priority order. Colour is
# reserved for this health signal alone — group membership is conveyed by the
# labelled dividers, not by tinting the row.
STATE_GLYPHS = {
    "conflict": ("⚠", SOFT_ROSE),
    "waiting": ("•", SOFT_GOLD),
    "ready": ("✓", SOFT_GREEN),
    "unknown": ("?", DIM),
    "draft": ("◦", DIM),
}
STATE_LABELS = {
    "conflict": "conflicts",
    "waiting": "waiting",
    "ready": "ready",
    "unknown": "unconfirmed",
    "draft": "draft",
}

CHECK_LABELS = {"success": "✓", "failure": "✗", "pending": "•"}
CHECK_COLORS = {
    "success": SOFT_GREEN,
    "failure": SOFT_ROSE,
    "pending": SOFT_GOLD,
}

# The single source of truth for the terminal layout: Title is elastic and
# sized to the terminal width by :func:`_columns`; every other column is fixed.
# Each entry is ``(header, width, align)`` — the same shape the issues renderer
# uses, so header and rows stay in lock-step.
_FIXED_BEFORE_TITLE: list[tuple[str, int, str]] = [("", 1, "left"), ("PR", 6, "left")]
_FIXED_AFTER_TITLE: list[tuple[str, int, str]] = [
    ("Assignee", 16, "left"),
    ("Age", 4, "right"),
    ("Last", 4, "right"),
    ("Review", 13, "left"),
    ("CI", 2, "left"),
    ("Cmt", 3, "right"),
]


def _columns(width: int) -> list[tuple[str, int, str]]:
    """The terminal columns for a ``width``-wide terminal, Title absorbing slack.

    Every fixed column keeps its width; Title takes whatever is left after the
    fixed columns and the inter-column gaps, clamped to [MIN, MAX]. Below the
    minimum the table simply overflows the terminal rather than dropping columns.
    """
    fixed = _FIXED_BEFORE_TITLE + _FIXED_AFTER_TITLE
    fixed_width = sum(w for _, w, _ in fixed)
    gaps = COLUMN_GAP * len(fixed)  # one gap per boundary, including Title's
    title = max(MIN_TITLE_WIDTH, width - fixed_width - gaps)
    return [
        *_FIXED_BEFORE_TITLE,
        ("Title", min(title, MAX_TITLE_WIDTH), "left"),
        *_FIXED_AFTER_TITLE,
    ]


def _changes_requested(row: PrRow) -> bool:
    return "changes requested" in row.review_status


def _review_text(row: PrRow) -> str:
    """The Review cell word: what still stands between this PR and merge."""
    if _changes_requested(row):
        return "changes req"
    if row.review_status == "approved":
        return "approved"
    if row.i_approved and not row.is_mine:
        return "you ✓"
    return "pending"


def _review_color(row: PrRow) -> str:
    if _changes_requested(row):
        return SOFT_ROSE
    if row.review_status == "approved":
        return SOFT_GREEN
    if row.i_approved and not row.is_mine:
        return SOFT_GREEN
    return SOFT_GOLD  # pending — light gold, matching the waiting glyph


def _check_label(row: PrRow) -> str:
    return CHECK_LABELS.get(row.check_state, "")


def _format_comments(count: int) -> str:
    # The Cmt column is 3 cells wide; a four-digit count would silently
    # truncate to a wrong number, so cap it honestly instead.
    return "99+" if count > 99 else str(count)


def _display_assignees_plain(row: PrRow) -> str:
    """The Assignee cell as plain text (markdown, and the terminal's basis)."""
    if row.is_release_pr:
        return "Release PR"
    if row.bot_name:
        return row.bot_name
    if row.assignees:
        return ", ".join(row.assignees)
    return ""


def _assignee_cell(row: PrRow, width: int, use_color: bool) -> str:
    """The terminal Assignee cell — weight-not-health, save the Release-PR tint.

    Assignee buckets are encoded by weight, not health colour: people you might
    chase stay at full weight, everything else dims back. Release PRs are the one
    exception — a soft, non-health blue flags the release train.
    """
    if row.is_release_pr:
        return colorize("Release PR", SOFT_BLUE, use_color)
    if row.bot_name:
        return dim(truncate(row.bot_name, width), use_color)
    if row.assignees:
        text = truncate(", ".join(row.assignees), width)
        # Dim only when there's nobody to chase but yourself.
        return dim(text, use_color) if row.assignees == ["me"] else text
    return ""


def _pr_row_line(
    row: PrRow,
    columns: list[tuple[str, int, str]],
    width_by: dict[str, int],
    gap: str,
    use_color: bool,
    use_links: bool,
) -> str:
    """One PR data row, dimmed whole when it is neither yours nor to-review.

    Shared by :func:`terminal_table` and :func:`owner_table`. "The rest" —
    settled work in the repo view, non-actionable rows in the owner view — is
    rendered plainly and wrapped in a single dim span, so no health colour
    competes with the muting. The two views differ only in how rows are grouped
    (labelled group dividers vs. repo sections), not in how a row is drawn.
    """
    muted = sort_group(row) == 2
    cc = use_color and not muted

    glyph, glyph_color = STATE_GLYPHS[pr_state(row)]
    # Age (days open) is context, never a call to action — always dim. The
    # Last cell carries both urgency signals: rose when stale, full weight
    # when the other side moved last (the days are *your* lag), dim when you
    # moved last or the direction is unknown.
    last_text = format_age(row.last_activity_days)
    if last_text and row.is_stale:
        last = colorize(last_text, SOFT_ROSE, cc)
    elif row.last_activity_mine is False:
        last = last_text
    else:
        last = dim(last_text, cc)
    values = [
        colorize(glyph, glyph_color, cc),
        dim(
            hyperlink(
                truncate(f"#{row.number}", width_by["PR"]),
                row.url,
                use_links,
            ),
            cc,
        ),
        truncate(row.title, width_by["Title"]),
        _assignee_cell(row, width_by["Assignee"], cc),
        dim(format_age(row.age_days), cc),
        last,
        colorize(_review_text(row), _review_color(row), cc),
        colorize(_check_label(row), CHECK_COLORS.get(row.check_state, ""), cc),
        dim(_format_comments(row.comments_count), cc),
    ]
    line = gap.join(
        format_cell(v, w, a)
        for v, (_, w, a) in zip(values, columns, strict=True)
    )
    return dim(line, use_color) if muted else line


def terminal_table(
    rows: list[PrRow], use_color: bool, use_links: bool = False
) -> str:
    """The terminal PR table: bold header, labelled group dividers, one row each.

    ``use_color`` gates every ANSI escape; ``use_links`` independently gates the
    OSC-8 hyperlink on the PR number (interactive stdout only — the wiring
    decides, this just accepts the flag). Rows are sorted into the yours /
    to-review / the-rest groups; each group opens with a divider carrying its
    label and count, and the-rest rows are rendered plain then dimmed whole.
    """
    columns = _columns(terminal_width())
    width_by = {name: w for name, w, _ in columns}
    total = sum(w for _, w, _ in columns) + COLUMN_GAP * (len(columns) - 1)
    gap = " " * COLUMN_GAP

    header = gap.join(format_cell(n, w, a) for n, w, a in columns)
    lines = [bold(header, use_color)]

    group_sizes = {g: sum(1 for r in rows if sort_group(r) == g) for g in (0, 1, 2)}
    previous_group: int | None = None
    for row in sorted(rows, key=sort_key):
        group = sort_group(row)
        if group != previous_group:
            label = f"{GROUP_LABELS[group]} ({group_sizes[group]})"
            lines.append(divider(label, total, use_color))
        previous_group = group
        lines.append(
            _pr_row_line(row, columns, width_by, gap, use_color, use_links)
        )

    return "\n".join(line.rstrip() for line in lines)


def owner_table(
    sections: list[tuple[str, list[PrRow]]],
    use_color: bool,
    use_links: bool = False,
) -> str:
    """The terminal owner-wide PR table: one header, a divider + rows per repo.

    ``sections`` is ``model.group_by_repo``'s output (``OWNER/REPO`` -> rows),
    already ordered most-recently-active first; that order is preserved, and so
    is each section's within-repo fetch order. Each section opens with a divider
    carrying ``OWNER/REPO · N open`` — the same idiom the issues owner view uses,
    and the grouping here, so there are no yours/to-review/the-rest sub-dividers.
    A row that is neither yours nor to-review is still dimmed whole (via
    :func:`_pr_row_line`), the same attention discipline as the repo view.
    """
    columns = _columns(terminal_width())
    width_by = {name: w for name, w, _ in columns}
    total = sum(w for _, w, _ in columns) + COLUMN_GAP * (len(columns) - 1)
    gap = " " * COLUMN_GAP

    header = gap.join(format_cell(n, w, a) for n, w, a in columns)
    lines = [bold(header, use_color)]

    for repo, rows in sections:
        lines.append(divider(f"{repo} · {len(rows)} open", total, use_color))
        for row in rows:
            lines.append(
                _pr_row_line(row, columns, width_by, gap, use_color, use_links)
            )

    return "\n".join(line.rstrip() for line in lines)


def summary_line(summary: PrSummary) -> str:
    """The one-line TLDR shown above the table, built from the model's counts.

    Zero counts are suppressed — the summary flags what needs attention, it
    doesn't enumerate every category.
    """
    parts = [f"{summary.open} open"]
    if summary.to_review:
        parts.append(f"{summary.to_review} to review")
    if summary.conflicts:
        parts.append(f"{summary.conflicts} with conflicts")
    if summary.failing_ci:
        parts.append(f"{summary.failing_ci} failing CI")
    if summary.your_move:
        parts.append(f"{summary.your_move} your move")
    return " · ".join(parts)


def symbol_key(use_color: bool) -> str:
    """The ``--show-key`` text: the states, the CI glyphs, and the dimming note.

    Matches the issues views' key idiom (bold ``Key``, aligned label columns, a
    trailing dim note); glyphs are tinted with their real colours so the key
    teaches both symbol and colour at once.
    """
    state_order = ["conflict", "waiting", "ready", "unknown", "draft"]
    states = "   ".join(
        f"{colorize(STATE_GLYPHS[s][0], STATE_GLYPHS[s][1], use_color)} "
        f"{STATE_LABELS[s]}"
        for s in state_order
    )
    ci_order = [("success", "pass"), ("failure", "fail"), ("pending", "pending")]
    ci = "   ".join(
        f"{colorize(CHECK_LABELS[key], CHECK_COLORS[key], use_color)} {label}"
        for key, label in ci_order
    )
    return "\n".join(
        [
            bold("Key", use_color),
            "  State   " + states,
            "  CI      " + ci,
            dim(
                "  Age = days open · Last = days since last human move · "
                "bright Last = their move, yours to answer",
                use_color,
            ),
            dim("  Last in rose = stale · dimmed rows = settled", use_color),
        ]
    )


def owner_key(use_color: bool) -> str:
    """The ``--show-key`` text for the owner-wide PR view — distinct from
    :func:`symbol_key`.

    Same health/CI glyphs and columns as the repo view (the owner view keeps
    them unchanged), but rows are grouped by repository — most recently active
    repo first — rather than yours/to-review/the-rest, and a row that is neither
    yours nor to-review is dimmed whole.
    """
    state_order = ["conflict", "waiting", "ready", "unknown", "draft"]
    states = "   ".join(
        f"{colorize(STATE_GLYPHS[s][0], STATE_GLYPHS[s][1], use_color)} "
        f"{STATE_LABELS[s]}"
        for s in state_order
    )
    ci_order = [("success", "pass"), ("failure", "fail"), ("pending", "pending")]
    ci = "   ".join(
        f"{colorize(CHECK_LABELS[key], CHECK_COLORS[key], use_color)} {label}"
        for key, label in ci_order
    )
    return "\n".join(
        [
            bold("Key", use_color),
            "  State   " + states,
            "  CI      " + ci,
            dim(
                "  Age = days open · Last = days since last human move · "
                "bright Last = their move, yours to answer",
                use_color,
            ),
            dim(
                "  Rows are grouped by repository, most recently active repo "
                "first · Last in rose = stale · dimmed rows are neither yours "
                "nor to review",
                use_color,
            ),
        ]
    )


def _markdown_signals(row: PrRow) -> list[str]:
    """The Signal column: the scan cues the terminal carries by weight/colour."""
    signals: list[str] = []
    if row.is_mine:
        signals.append("mine")
    if row.is_to_review:
        signals.append("To Review")
    if row.is_release_pr:
        signals.append("Release PR")
    if your_move(row):
        signals.append("your move")
    return signals


def markdown_table(rows: list[PrRow]) -> str:
    """The markdown PR table — colour-free, so state words and a Signal column
    carry what the terminal conveys by glyph, weight, and hue. Pipes in any cell
    are escaped so a ``|`` in a title can't break the table.
    """
    lines = [
        "| PR ID | Title | State | Assignee | Age | Last | Review | CI | "
        "Comments | Signal |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for row in sorted(rows, key=sort_key):
        pr_id = f"[#{row.number}]({row.url})" if row.url else f"#{row.number}"
        title = escape_markdown_cell(row.title)
        assignees = escape_markdown_cell(_display_assignees_plain(row))
        signal = escape_markdown_cell(", ".join(_markdown_signals(row)))
        lines.append(
            f"| {pr_id} | {title} | {STATE_LABELS[pr_state(row)]} | {assignees} | "
            f"{format_age(row.age_days)} | {format_age(row.last_activity_days)} | "
            f"{_review_text(row)} | {_check_label(row)} | "
            f"{row.comments_count} | {signal} |"
        )

    return "\n".join(lines)


def owner_markdown(sections: list[tuple[str, list[PrRow]]]) -> str:
    """Markdown owner-wide view: a ``## OWNER/REPO`` heading + table per section.

    Each section reuses :func:`markdown_table` (the same colour-free table the
    repo view emits, Signal column and all), so a reader gets the full per-repo
    breakdown. Sections keep ``model.group_by_repo``'s most-recently-active-first
    order — the markdown counterpart of :func:`owner_table`.
    """
    return "\n\n".join(
        f"## {repo}\n\n{markdown_table(rows)}" for repo, rows in sections
    )
