"""Tests for plate.prs.render — the pure PR render layer.

Ported from gh-pr-status's tests/test_cli.py, keeping the render-behaviour
assertions (glyph selection per state, group divider labels + counts and
ordering, whole-row dimming of "the rest", the Release-PR blue tint, bot/me
dimming, stale-age rose, summary zero-suppression, markdown escaping + the
signal column, the 99+ cap, opt-in hyperlinks). Rebuilt against the fresh
renderer at the behavioural-parity bar (#53): assertions are on *behaviour*
(substrings, ANSI codes present/absent, ordering), never byte-exact spacing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plate.core.render import (
    BOLD,
    DIM,
    RESET,
    SOFT_BLUE,
    SOFT_GOLD,
    SOFT_GREEN,
    SOFT_ROSE,
    hyperlink,
    visible_length,
)
from plate.prs import model, render

# A real release-please title for this repo (see model._RELEASE_PR_RE).
RELEASE_TITLE = "chore(main): release 0.5.0"


def pr(
    number: int,
    title: str,
    assignees: list[str] | None = None,
    is_draft: bool = False,
    review_decision: str | None = None,
    comments: int = 0,
    latest_reviews: list[dict[str, object]] | None = None,
    author: str | None = None,
    updated_at: str | None = None,
    mergeable: str | None = None,
    rollup: str | None = None,
    author_type: str = "User",
) -> dict[str, object]:
    """A PR node in the GraphQL shape the fetch layer produces."""
    return {
        "number": number,
        "url": f"https://github.com/acme/widget/pull/{number}",
        "title": title,
        "isDraft": is_draft,
        "assignees": {"nodes": [{"login": login} for login in assignees or []]},
        "reviewDecision": review_decision,
        "latestReviews": {"nodes": latest_reviews or []},
        "reviewRequests": {"nodes": []},
        "author": {"login": author, "__typename": author_type} if author else None,
        "updatedAt": updated_at,
        "mergeable": mergeable,
        "totalCommentsCount": comments,
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "statusCheckRollup": {"state": rollup} if rollup else None
                    }
                }
            ]
        },
    }


def review(login: str, state: str) -> dict[str, object]:
    return {"author": {"login": login}, "state": state}


def rows_for(*prs: dict[str, object], login: str = "simon", **kwargs: object):
    return model.normalize_rows(list(prs), login, **kwargs)  # type: ignore[arg-type]


# --- state glyphs ------------------------------------------------------------


def test_state_glyphs_carry_health_colour() -> None:
    rows = rows_for(
        pr(2, "Ready", ["simon"], review_decision="APPROVED", rollup="SUCCESS"),
        pr(1, "Waiting on review", ["simon"]),
    )
    output = render.terminal_table(rows, use_color=True)
    # The leading glyph carries the only health colour on the row.
    assert f"{SOFT_GREEN}✓{RESET}" in output
    assert f"{SOFT_GOLD}•{RESET}" in output
    # The PR id is demoted to a dim reference, not coloured by group.
    assert f"{DIM}#1{RESET}" in output


def test_conflict_glyph_is_rose() -> None:
    rows = rows_for(pr(1, "Conflicting", ["alice"], mergeable="CONFLICTING"))
    assert model.pr_state(rows[0]) == "conflict"
    assert f"{SOFT_ROSE}⚠{RESET}" in render.terminal_table(rows, use_color=True)


def test_draft_and_unknown_use_dim_glyphs() -> None:
    rows = rows_for(
        pr(1, "Draft", ["simon"], is_draft=True),
        pr(
            2,
            "Recomputing",
            ["simon"],
            review_decision="APPROVED",
            rollup="SUCCESS",
            mergeable="UNKNOWN",
        ),
    )
    out = render.terminal_table(rows, use_color=True)
    assert f"{DIM}◦{RESET}" in out
    assert f"{DIM}?{RESET}" in out


# --- hyperlinks and colour toggles -------------------------------------------


def test_pr_number_is_hyperlinked_only_when_enabled() -> None:
    url = "https://github.com/acme/widget/pull/1"
    rows = rows_for(pr(1, "Mine", ["simon"]))

    linked = render.terminal_table(rows, use_color=False, use_links=True)
    assert f"\033]8;;{url}\033\\#1\033]8;;\033\\" in linked
    # The escapes take no cells, so layout math is unaffected.
    assert visible_length(hyperlink("#1", url, True)) == 2
    # Links are opt-in: pipes and files get plain text.
    assert "\033]8" not in render.terminal_table(rows, use_color=False)


def test_terminal_color_can_be_disabled() -> None:
    rows = rows_for(pr(1, "Mine", ["simon"]))
    output = render.terminal_table(rows, use_color=False)

    assert "\033[" not in output
    assert "#1" in output
    assert "me" in output


# --- review column -----------------------------------------------------------


def test_review_options_are_colour_coded() -> None:
    rows = rows_for(
        pr(1, "approved", ["simon"], review_decision="APPROVED"),
        pr(2, "changes", ["simon"], review_decision="CHANGES_REQUESTED"),
        pr(3, "pending", ["simon"]),
    )
    out = render.terminal_table(rows, use_color=True)
    assert f"{SOFT_GREEN}approved{RESET}" in out
    assert f"{SOFT_ROSE}changes req{RESET}" in out
    assert f"{SOFT_GOLD}pending{RESET}" in out


def test_review_shows_you_approved_when_i_reviewed() -> None:
    rows = rows_for(
        pr(
            1,
            "I reviewed someone else's PR",
            ["alice"],
            review_decision=None,
            latest_reviews=[review("simon", "APPROVED")],
        ),
    )
    output = render.terminal_table(rows, use_color=True)
    assert f"{SOFT_GREEN}you ✓{RESET}" in output


# --- age / staleness ---------------------------------------------------------


def test_stale_age_is_rose_fresh_is_dim() -> None:
    now = datetime(2026, 6, 19, tzinfo=UTC)
    fresh = (now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    old = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    rows = rows_for(
        pr(1, "Fresh", ["alice"], updated_at=fresh),
        pr(2, "Stale", ["alice"], updated_at=old),
        now=now,
        stale_days=14,
    )
    output = render.terminal_table(rows, use_color=True)
    # A stale age is highlighted rose (4w for 30 days); a fresh one is dimmed.
    assert f"{SOFT_ROSE}4w{RESET}" in output
    assert f"{DIM}2d{RESET}" in output


# --- assignee column ---------------------------------------------------------


def test_assignee_me_only_is_dimmed() -> None:
    rows = rows_for(pr(1, "Mine", ["simon"]))
    assert render._display_assignees_plain(rows[0]) == "me"
    assert f"{DIM}me{RESET}" in render.terminal_table(rows, use_color=True)


def test_chaseable_human_assignee_stays_full_weight() -> None:
    rows = rows_for(pr(1, "Mine, alice's to land", ["alice"], author="simon"))
    assert rows[0].is_mine
    assert render._display_assignees_plain(rows[0]) == "alice"
    # Alice is chaseable, so she stays at full weight (never dimmed).
    assert f"{DIM}alice" not in render.terminal_table(rows, use_color=True)


def test_release_pr_has_soft_blue_marker() -> None:
    # An open release PR is unapproved, so it sits in "to review" (active) where
    # its soft-blue marker shows at full weight.
    rows = rows_for(pr(1, RELEASE_TITLE))
    out = render.terminal_table(rows, use_color=True)
    assert f"{SOFT_BLUE}Release PR{RESET}" in out


def test_bot_authors_are_dimmed() -> None:
    rows = rows_for(pr(1, "Update all deps", author="app/renovate"))
    assert rows[0].bot_name == "renovate"
    assert render._display_assignees_plain(rows[0]) == "renovate"
    assert f"{DIM}renovate{RESET}" in render.terminal_table(rows, use_color=True)


# --- CI column ---------------------------------------------------------------


def test_ci_glyph_coloured_by_state() -> None:
    rows = rows_for(
        pr(1, "Failing", ["alice"], rollup="FAILURE"),
        pr(2, "Passing", ["simon"], review_decision="APPROVED", rollup="SUCCESS"),
    )
    output = render.terminal_table(rows, use_color=True)
    # #1 is to-review (full weight); #2 is yours (full weight) — neither dimmed,
    # so both CI glyphs show their real colour.
    assert f"{SOFT_ROSE}✗{RESET}" in output
    assert f"{SOFT_GREEN}✓{RESET}" in output


def test_ci_column_alignment_survives_empty_cells() -> None:
    rows = rows_for(
        pr(1, "Passing", ["alice"], rollup="SUCCESS"),
        pr(2, "No checks", ["alice"]),
    )
    # Skip header + divider; a row with a CI glyph must line up with a row whose
    # CI cell is empty (both in the same group so neither is dimmed whole).
    data_lines = [
        line
        for line in render.terminal_table(rows, use_color=False).splitlines()
        if line and not line.startswith("── ")
    ][1:]
    widths = {visible_length(line) for line in data_lines}
    assert len(widths) == 1


# --- header, dividers, ordering ----------------------------------------------


def test_bold_header_and_unicode_rule() -> None:
    rows = rows_for(pr(1, "Mine", ["simon"]))
    output = render.terminal_table(rows, use_color=True)
    assert BOLD in output
    assert "─" in output
    assert "---" not in output


def test_labeled_group_dividers_carry_counts_and_order() -> None:
    rows = rows_for(
        pr(1, "Mine", ["simon"]),
        pr(2, "To review", ["alice"]),
        pr(3, "Someone else's", ["bob"], review_decision="APPROVED"),
    )
    lines = render.terminal_table(rows, use_color=False).splitlines()
    dividers = [line for line in lines if line.startswith("── ")]
    assert len(dividers) == 3
    # Each divider carries its group's row count, in the fixed group order.
    assert dividers[0].startswith("── yours (1) ")
    assert dividers[1].startswith("── to review (1) ")
    assert dividers[2].startswith("── the rest (1) ")


def test_the_rest_group_is_dimmed_whole() -> None:
    rows = rows_for(pr(1, "Settled", ["bob"], review_decision="APPROVED"))
    output = render.terminal_table(rows, use_color=True)
    data_line = output.splitlines()[-1]
    # A settled row is rendered plainly then wrapped in a single dim span — no
    # health colour competes with the muting.
    assert data_line.startswith(DIM)
    assert SOFT_GREEN not in data_line


# --- layout ------------------------------------------------------------------


def test_terminal_layout_fills_the_terminal_exactly() -> None:
    # When Title is between its clamps, columns + gaps must total exactly the
    # terminal width — the invariant the elastic column exists to hold.
    for width in (80, 100, 108):
        columns = render._columns(width)
        total = sum(w for _, w, _ in columns) + render.COLUMN_GAP * (len(columns) - 1)
        title = {name: w for name, w, _ in columns}["Title"]
        assert render.MIN_TITLE_WIDTH < title < render.MAX_TITLE_WIDTH
        assert total == width
    # Outside the clamps the Title pins to its bounds.
    assert {n: w for n, w, _ in render._columns(300)}["Title"] == render.MAX_TITLE_WIDTH
    assert {n: w for n, w, _ in render._columns(40)}["Title"] == render.MIN_TITLE_WIDTH


def test_comment_count_right_aligned() -> None:
    rows = rows_for(pr(1, "Mine", ["simon"], comments=7))
    data_line = render.terminal_table(rows, use_color=False).splitlines()[-1]
    # Right-aligned: the count sits flush at the end with no trailing pad.
    assert data_line.endswith("7")


def test_comment_count_capped_at_99_plus() -> None:
    assert render._format_comments(0) == "0"
    assert render._format_comments(99) == "99"
    assert render._format_comments(1234) == "99+"
    rows = rows_for(pr(1, "Very chatty", ["simon"], comments=150))
    output = render.terminal_table(rows, use_color=False)
    assert "99+" in output
    assert "150" not in output


# --- summary line ------------------------------------------------------------


def test_summary_line_reports_all_four_figures() -> None:
    rows = rows_for(
        pr(1, "Mine", ["simon"]),
        pr(2, "To review", ["alice"]),
        pr(3, "Conflicting", ["alice"], mergeable="CONFLICTING"),
        pr(4, "Failing", ["bob"], review_decision="APPROVED", rollup="FAILURE"),
    )
    line = render.summary_line(model.summary_counts(rows))
    assert line == "4 open · 2 to review · 1 with conflicts · 1 failing CI"


def test_summary_line_suppresses_zero_counts() -> None:
    rows = rows_for(pr(1, "Mine", ["simon"]))
    assert render.summary_line(model.summary_counts(rows)) == "1 open"


# --- symbol key --------------------------------------------------------------


def test_symbol_key_teaches_glyphs_and_colours() -> None:
    key = render.symbol_key(use_color=True)
    assert "Key" in key
    for label in render.STATE_LABELS.values():
        assert label in key
    assert "pass" in key
    assert "fail" in key
    assert "stale" in key
    # Glyphs are tinted with their real colours so the key teaches both.
    assert f"{SOFT_GREEN}✓{RESET}" in key
    assert f"{SOFT_ROSE}⚠{RESET}" in key


def test_symbol_key_plain_when_color_disabled() -> None:
    assert "\033[" not in render.symbol_key(use_color=False)


# --- markdown ----------------------------------------------------------------


def test_markdown_keeps_scan_signals() -> None:
    rows = rows_for(
        pr(1, "Mine", ["simon"]),
        pr(2, "To review", ["alice"]),
        pr(3, RELEASE_TITLE, review_decision="APPROVED"),
    )
    output = render.markdown_table(rows)
    assert (
        "| PR ID | Title | State | Assignee | Age | Review | CI | Comments | "
        "Signal |"
    ) in output
    assert (
        "| [#1](https://github.com/acme/widget/pull/1) | Mine | "
        "waiting | me |  | pending |  | 0 | mine |"
    ) in output
    assert (
        "| [#2](https://github.com/acme/widget/pull/2) | To review | "
        "waiting | alice |  | pending |  | 0 | To Review |"
    ) in output
    # The release PR reads as "ready" only after approval; its signal is kept.
    assert "| Release PR |" in output


def test_markdown_escapes_pipes_in_cells() -> None:
    rows = rows_for(pr(1, "Fix a | b parsing", ["simon"]))
    output = render.markdown_table(rows)
    assert r"Fix a \| b parsing" in output
    # An unescaped pipe would add a spurious column; the escape prevents that.
    assert "Fix a | b parsing" not in output


def test_markdown_has_no_ansi() -> None:
    rows = rows_for(pr(1, "Mine", ["simon"], review_decision="APPROVED"))
    assert "\033[" not in render.markdown_table(rows)


# --- owner-wide view ----------------------------------------------------------


def _in_repo(node: dict[str, object], repo: str) -> dict[str, object]:
    """An owner-search node: the PR payload plus its own repository field."""
    return {**node, "repository": {"nameWithOwner": repo}}


def owner_sections() -> list[tuple[str, list[model.PrRow]]]:
    # repo-b (most recently active, listed first): one mine + one to-review;
    # repo-a: a single settled row (neither mine nor to-review -> dimmed).
    rows = model.normalize_rows(
        [
            _in_repo(pr(1, "Mine here", ["simon"]), "acme/repo-b"),
            _in_repo(pr(2, "Review me", ["alice"]), "acme/repo-b"),
            _in_repo(
                pr(3, "Settled elsewhere", ["bob"], review_decision="APPROVED"),
                "acme/repo-a",
            ),
        ],
        "simon",
    )
    return model.group_by_repo(rows)


def test_owner_table_sections_in_order_with_open_counts() -> None:
    out = render.owner_table(owner_sections(), use_color=False)
    assert out.index("acme/repo-b") < out.index("acme/repo-a")  # order kept
    # divider carries OWNER/REPO plus the section's open count
    assert "── acme/repo-b · 2 open" in out
    assert "── acme/repo-a · 1 open" in out


def test_owner_table_has_no_group_subdividers() -> None:
    # The repo divider is the grouping here — no yours/to-review/the-rest.
    out = render.owner_table(owner_sections(), use_color=False)
    assert "── yours" not in out
    assert "── to review" not in out
    assert "── the rest" not in out


def test_owner_table_dims_rows_neither_mine_nor_to_review() -> None:
    out = render.owner_table(owner_sections(), use_color=True)
    settled_line = next(line for line in out.splitlines() if "Settled" in line)
    assert settled_line.startswith(DIM)  # whole line dimmed
    assert SOFT_GREEN not in settled_line  # no health colour competes


def test_owner_table_keeps_mine_and_to_review_full_weight() -> None:
    out = render.owner_table(owner_sections(), use_color=True)
    mine_line = next(line for line in out.splitlines() if "Mine here" in line)
    review_line = next(line for line in out.splitlines() if "Review me" in line)
    assert not mine_line.startswith(DIM)
    assert not review_line.startswith(DIM)
    # Health glyphs keep their colours on full-weight rows.
    assert f"{SOFT_GOLD}•{RESET}" in mine_line


def test_owner_table_rows_keep_section_fetch_order() -> None:
    lines = render.owner_table(owner_sections(), use_color=False).splitlines()
    assert (
        next(i for i, line in enumerate(lines) if "Mine here" in line)
        < next(i for i, line in enumerate(lines) if "Review me" in line)
    )


def test_owner_table_color_can_be_disabled() -> None:
    assert "\033[" not in render.owner_table(owner_sections(), use_color=False)


def test_owner_markdown_headings_per_repo_with_tables() -> None:
    out = render.owner_markdown(owner_sections())
    assert "## acme/repo-b" in out
    assert "## acme/repo-a" in out
    assert out.index("## acme/repo-b") < out.index("## acme/repo-a")
    # each section reuses the repo view's markdown table
    assert out.count("| PR ID | Title |") == 2
    assert "\033[" not in out


def test_owner_key_teaches_repo_grouping_and_dimming() -> None:
    key = render.owner_key(use_color=True)
    assert "Key" in key
    assert "grouped by repository" in key
    assert "neither yours nor to review" in key
    for label in render.STATE_LABELS.values():
        assert label in key
    assert f"{SOFT_GREEN}✓{RESET}" in key


def test_owner_key_plain_when_color_disabled() -> None:
    assert "\033[" not in render.owner_key(use_color=False)
