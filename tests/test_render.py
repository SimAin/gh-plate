"""Tests for issue_check.render — the pure presentation/tree layer."""

from __future__ import annotations

from issue_check import render
from issue_check.model import IssueRow, TreeNode


def make_row(
    number: int,
    *,
    mine: bool = True,
    title: str = "Title",
    age_days: int = 30,
    is_stale: bool = True,
    labels: list[str] | None = None,
    comments: int = 0,
    parent_number: int | None = None,
    sub_total: int = 0,
    sub_completed: int = 0,
    pr_state: str | None = None,
    pr_number: int | None = None,
) -> IssueRow:
    return IssueRow(
        number=number,
        url=f"https://github.com/an-org/a-repo/issues/{number}",
        title=title,
        labels=labels or [],
        comments_count=comments,
        age_days=age_days,
        is_stale=is_stale,
        parent_number=parent_number,
        sub_total=sub_total,
        sub_completed=sub_completed,
        mine=mine,
        pr_state=pr_state,
        pr_number=pr_number,
    )


def sample_forest() -> list[TreeNode]:
    # Un-owned epic with one owned child, plus a standalone owned issue.
    child = TreeNode(make_row(11, title="Child task", age_days=50), depth=1)
    epic = TreeNode(
        make_row(10, mine=False, title="Epic", sub_total=4, sub_completed=1),
        depth=0,
        children=[child],
    )
    standalone = TreeNode(
        make_row(3, title="Standalone", age_days=5, is_stale=False), depth=0
    )
    return [epic, standalone]


# --- primitives --------------------------------------------------------------

def test_format_age_units() -> None:
    assert render.format_age(3) == "3d"
    assert render.format_age(21) == "3w"
    assert render.format_age(90) == "3mo"
    assert render.format_age(None) == ""


def test_format_age_year_boundaries() -> None:
    assert render.format_age(364) == "12mo"
    assert render.format_age(365) == "1y"
    assert render.format_age(729) == "1y"
    assert render.format_age(730) == "2y"


def test_truncate_uses_ellipsis() -> None:
    assert render.truncate("hello world", 8) == "hello w…"
    assert render.truncate("short", 10) == "short"


def test_format_labels_packs_whole_labels_with_overflow_count() -> None:
    # one whole label fits, the rest become +N (never mashed/mid-word)
    assert render.format_labels(["security", "automation"], 18) == "security +1"
    assert render.format_labels(
        ["epic", "needs refinement", "Vulnerability Discovery"], 18
    ) == "epic +2"
    assert render.format_labels(
        ["infrastructure", "feature", "performance"], 18
    ) == "infrastructure +2"


def test_format_labels_keeps_multiple_short_labels() -> None:
    assert render.format_labels(["ux", "bug"], 18) == "ux · bug"


def test_format_labels_single_label_cases() -> None:
    assert render.format_labels([], 18) == ""
    assert render.format_labels(["epic"], 18) == "epic"
    # a lone label longer than the column is the only thing ellipsis-truncated
    assert render.format_labels(["Vulnerability Discovery"], 18) == "Vulnerability Dis…"


def test_format_labels_preserves_count_when_first_label_is_long() -> None:
    # first label nearly fills the column but more follow: keep the +N
    out = render.format_labels(["needs refinement", "Eng design task"], 18)
    assert out.endswith(" +1")
    assert render.visible_length(out) <= 18
    assert "…" in out


def test_format_labels_promotes_and_colours_special_label() -> None:
    resolver = {"blocked": "alert"}.get
    out = render.format_labels(
        ["bug", "blocked"], 18, use_color=True, resolver=resolver
    )
    visible = render.visible_text(out)
    assert visible.startswith("blocked")          # promoted to the front
    assert render.SOFT_ROSE in out                # alert -> red
    assert "bug" in visible                        # ordinary label still present


def test_format_labels_hides_hidden_labels() -> None:
    resolver = {"wontfix": "hide"}.get
    out = render.format_labels(
        ["wontfix", "bug"], 18, use_color=False, resolver=resolver
    )
    assert "wontfix" not in out
    assert out == "bug"


def test_format_labels_unchanged_without_resolver() -> None:
    # back-compat: no resolver behaves exactly like the plain packed cell
    assert render.format_labels(["security", "automation"], 18) == "security +1"


def test_markdown_bolds_special_label_and_drops_hidden() -> None:
    from issue_check.model import TreeNode

    row = make_row(5, title="T", labels=["blocked", "bug", "noise"])
    resolver = {"blocked": "alert", "noise": "hide"}.get
    out = render.markdown_tree([TreeNode(row, 0)], resolver)
    assert "**blocked**" in out                    # special, bolded + promoted
    assert "bug" in out
    assert "noise" not in out                       # hidden


def test_visible_length_ignores_ansi() -> None:
    colored = render.colorize("abc", render.SOFT_GOLD, True)
    assert render.visible_length(colored) == 3
    assert len(colored) > 3


def test_hyperlink_wraps_and_is_width_neutral() -> None:
    linked = render.hyperlink("#5", "https://x/5", enabled=True)
    assert "https://x/5" in linked
    assert linked != "#5"
    # the escape sequence must not count toward visible width
    assert render.visible_text(linked) == "#5"
    assert render.visible_length(linked) == 2


def test_hyperlink_noop_when_disabled_or_no_url() -> None:
    assert render.hyperlink("#5", "https://x/5", enabled=False) == "#5"
    assert render.hyperlink("#5", "", enabled=True) == "#5"


def test_visible_length_ignores_nested_link_and_color() -> None:
    # a dimmed number inside a hyperlink (as _tree_cell builds it)
    cell = render.hyperlink(render.dim("#5", True), "https://x/5", enabled=True)
    assert render.visible_length(cell) == 2


# --- terminal tree -----------------------------------------------------------

def test_terminal_tree_no_color_structure() -> None:
    out = render.terminal_tree(sample_forest(), use_color=False)
    assert "\033[" not in out                 # no ANSI when colour off
    assert "── yours ──" in out
    # un-owned epic marked · with blank health+PR columns before the number
    assert render.CONTEXT_GLYPH + "   #10" in out
    assert "•" in out                          # stale owned child
    assert "✓" in out                          # active standalone
    assert "1/4" in out                        # epic rollup shown


def test_terminal_tree_indents_children() -> None:
    lines = render.terminal_tree(sample_forest(), use_color=False).splitlines()
    epic_line = next(line for line in lines if "#10" in line)
    child_line = next(line for line in lines if "#11" in line)
    # child's glyph/number start further right than the parent's
    assert child_line.index("#11") > epic_line.index("#10")


def test_terminal_tree_color_emits_ansi() -> None:
    out = render.terminal_tree(sample_forest(), use_color=True)
    assert "\033[" in out


def test_terminal_tree_links_numbers_when_color_on() -> None:
    out = render.terminal_tree(sample_forest(), use_color=True)
    assert "\033]8;;" in out                                   # OSC-8 present
    assert "https://github.com/an-org/a-repo/issues/3" in out       # owned row link
    assert "https://github.com/an-org/a-repo/issues/10" in out      # context epic link


def test_terminal_tree_no_links_when_color_off() -> None:
    out = render.terminal_tree(sample_forest(), use_color=False)
    assert "\033]8;;" not in out
    assert "https://" not in out


def test_pr_marker_beside_health_glyph() -> None:
    from issue_check.model import PR_DRAFT, PR_MERGED

    forest = [
        TreeNode(make_row(5, title="Has draft PR", pr_state=PR_DRAFT, pr_number=99), 0),
        TreeNode(make_row(6, title="Merged but open", pr_state=PR_MERGED), 0),
        TreeNode(make_row(7, title="No PR"), 0),
    ]
    out = render.terminal_tree(forest, use_color=False)
    draft_line = next(line for line in out.splitlines() if "#5" in line)
    merged_line = next(line for line in out.splitlines() if "#6" in line)
    none_line = next(line for line in out.splitlines() if "#7" in line)
    assert render.PR_GLYPHS[PR_DRAFT][0] in draft_line
    assert render.PR_GLYPHS[PR_MERGED][0] in merged_line
    # the unlinked row carries no PR glyph at all
    assert render.PR_GLYPHS[PR_DRAFT][0] not in none_line
    assert render.PR_GLYPHS[PR_MERGED][0] not in none_line


def test_pr_marker_colours_merged_green_closed_red() -> None:
    from issue_check.model import PR_CLOSED, PR_MERGED, PR_OPEN

    merged = render._pr_marker(PR_MERGED, use_color=True)
    closed = render._pr_marker(PR_CLOSED, use_color=True)
    assert render.SOFT_GREEN in merged          # merged ⇄ tinted green
    assert render.SOFT_ROSE in closed           # closed ✗ tinted red
    assert render.PR_GLYPHS[PR_MERGED][0] == render.PR_GLYPHS[PR_OPEN][0]  # same glyph


def test_pr_marker_shown_in_markdown_with_number() -> None:
    from issue_check.model import PR_OPEN

    row = make_row(5, title="T", pr_state=PR_OPEN, pr_number=99)
    out = render.markdown_tree([TreeNode(row, 0)])
    assert f"{render.PR_GLYPHS[PR_OPEN][0]} #99" in out


def test_context_node_blank_age_owned_child_has_age() -> None:
    # Age is rendered for owned rows only; the un-owned epic's age cell is blank.
    lines = render.terminal_tree(sample_forest(), use_color=False).splitlines()
    epic_line = next(line for line in lines if "#10" in line)
    child_line = next(line for line in lines if "#11" in line)
    assert "7w" in child_line          # 50 days -> 7w on the owned child
    assert "7w" not in epic_line        # context epic carries no age


# --- markdown tree -----------------------------------------------------------

def test_markdown_tree_is_nested_list() -> None:
    out = render.markdown_tree(sample_forest())
    lines = out.splitlines()
    # owned child indented two spaces under the epic
    assert any(line.startswith("  - ") and "#11" in line for line in lines)
    # un-owned epic italicised, with rollup, at depth 0
    assert any(
        line.startswith("- *") and "#10" in line and "1/4" in line for line in lines
    )
    # owned rows carry a linked issue id and a glyph
    assert "[#3](https://github.com/an-org/a-repo/issues/3)" in out


def test_markdown_owned_row_has_glyph_and_meta() -> None:
    row = make_row(5, title="T", age_days=90, labels=["bug"], comments=2)
    out = render.markdown_tree([TreeNode(row, 0)])
    assert out.startswith("- • [#5]")
    assert "3mo" in out and "bug" in out and "2c" in out
