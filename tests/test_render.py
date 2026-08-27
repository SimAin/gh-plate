"""Tests for plate.issues.render — the pure presentation/tree layer."""

from __future__ import annotations

from plate.issues import render
from plate.issues.model import IssueRow, SprintRow, SprintView, TreeNode


def make_row(
    number: int,
    *,
    repo: str = "an-org/a-repo",
    mine: bool = True,
    context: bool | None = None,
    title: str = "Title",
    age_days: int = 30,
    is_stale: bool = True,
    labels: list[str] | None = None,
    labels_hidden: int = 0,
    comments: int = 0,
    parent_number: int | None = None,
    sub_total: int = 0,
    sub_completed: int = 0,
    assignees: list[str] | None = None,
    pr_state: str | None = None,
    pr_number: int | None = None,
) -> IssueRow:
    # In the yours view context is exactly "not mine"; owner-view tests that need
    # the two set independently (an un-owned, non-context row) pass context.
    if context is None:
        context = not mine
    return IssueRow(
        repo=repo,
        number=number,
        url=f"https://github.com/{repo}/issues/{number}",
        title=title,
        labels=labels or [],
        labels_hidden=labels_hidden,
        comments_count=comments,
        age_days=age_days,
        is_stale=is_stale,
        parent_number=parent_number,
        sub_total=sub_total,
        sub_completed=sub_completed,
        mine=mine,
        context=context,
        assignees=assignees or [],
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


def test_visible_length_counts_display_columns() -> None:
    # CJK and emoji render two columns each, not one code point
    assert render.visible_length("中文标题") == 8
    assert render.visible_length("🚀") == 2
    assert render.visible_length("🚀 fix") == 6  # 2 + space + 3
    # a variation selector adds no width to its base glyph
    assert render.visible_length("⚠️") == render.visible_length("⚠")


def test_format_cell_pads_double_width_to_same_columns() -> None:
    ascii_cell = render.format_cell("ab", 8)
    cjk_cell = render.format_cell("中文", 8)  # two glyphs, four columns
    assert render.visible_length(ascii_cell) == 8
    assert render.visible_length(cjk_cell) == 8
    assert cjk_cell == "中文    "  # four trailing spaces fill the remaining columns


def test_truncate_double_width_never_splits_glyph() -> None:
    # budget 5 lands mid-glyph: drop the straddling glyph, ellipsis fits the cut
    assert render.truncate("中文标题", 5) == "中文…"
    assert render.visible_length(render.truncate("中文标题", 5)) == 5
    # a value already within the column budget is returned untouched
    assert render.truncate("中文", 4) == "中文"
    # emoji title, odd budget: two columns for the glyph, one for the ellipsis
    assert render.truncate("🚀🚀🚀", 3) == "🚀…"


def test_format_labels_packs_whole_labels_with_overflow_count() -> None:
    # one whole label fits, the rest become +N (never mashed/mid-word)
    assert render.format_labels(["security", "automation"], 18) == "security +1"
    assert (
        render.format_labels(
            ["epic", "needs refinement", "Vulnerability Discovery"], 18
        )
        == "epic +2"
    )
    assert (
        render.format_labels(["infrastructure", "feature", "performance"], 18)
        == "infrastructure +2"
    )


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
    assert visible.startswith("blocked")  # promoted to the front
    assert render.SOFT_ROSE in out  # alert -> red
    assert "bug" in visible  # ordinary label still present


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


def test_format_labels_hidden_tail_appears_without_in_fetch_overflow() -> None:
    # everything fetched fits, but 3 labels were never fetched -> +3
    assert render.format_labels(["ux", "bug"], 18, hidden=3) == "ux · bug +3"


def test_format_labels_hidden_tail_sums_with_in_fetch_overflow() -> None:
    # one label fits (in-fetch overflow +1) plus a 4-label tail -> +5
    out = render.format_labels(["security", "automation"], 18, hidden=4)
    assert out == "security +5"


def test_format_labels_hidden_tail_only_when_nothing_visible() -> None:
    # all fetched labels were emoji-only-stripped away, but a tail remains
    assert render.format_labels([], 18, hidden=2) == "+2"


def test_format_labels_hidden_zero_is_byte_identical() -> None:
    # tail 0 must leave the existing behaviour untouched
    assert render.format_labels(["security", "automation"], 18, hidden=0) == (
        "security +1"
    )
    assert render.format_labels([], 18, hidden=0) == ""


def test_pack_labels_folds_hidden_into_count() -> None:
    assert render._pack_labels(["ux", "bug"], 18, 3) == "ux · bug +3"
    assert render._pack_labels([], 18, 2) == "+2"
    assert render._pack_labels(["ux", "bug"], 18, 0) == "ux · bug"


def test_markdown_bolds_special_label_and_drops_hidden() -> None:
    from plate.issues.model import TreeNode

    row = make_row(5, title="T", labels=["blocked", "bug", "noise"])
    resolver = {"blocked": "alert", "noise": "hide"}.get
    out = render.markdown_tree([TreeNode(row, 0)], resolver)
    assert "**blocked**" in out  # special, bolded + promoted
    assert "bug" in out
    assert "noise" not in out  # hidden


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
    assert "\033[" not in out  # no ANSI when colour off
    assert "── yours (2) ──" in out  # counts owned rows, not the context epic
    # un-owned epic marked · with blank health+PR columns before the number
    assert render.CONTEXT_GLYPH + "   #10" in out
    assert "•" in out  # stale owned child
    assert "✓" in out  # active standalone
    assert "1/4" in out  # epic rollup shown


def test_terminal_tree_indents_children() -> None:
    lines = render.terminal_tree(sample_forest(), use_color=False).splitlines()
    epic_line = next(line for line in lines if "#10" in line)
    child_line = next(line for line in lines if "#11" in line)
    # child's glyph/number start further right than the parent's
    assert child_line.index("#11") > epic_line.index("#10")


def test_terminal_tree_color_emits_ansi() -> None:
    out = render.terminal_tree(sample_forest(), use_color=True)
    assert "\033[" in out


def test_terminal_tree_links_numbers_when_links_on() -> None:
    out = render.terminal_tree(sample_forest(), use_color=False, use_links=True)
    assert "\033]8;;" in out  # OSC-8 present
    assert "https://github.com/an-org/a-repo/issues/3" in out  # owned row link
    assert "https://github.com/an-org/a-repo/issues/10" in out  # context epic link


def test_terminal_tree_links_are_independent_of_color() -> None:
    out = render.terminal_tree(sample_forest(), use_color=True)
    assert "\033[" in out
    assert "\033]8;;" not in out
    assert "https://" not in out


def test_sprint_and_owner_views_link_only_when_links_on() -> None:
    sprint_on = render.sprint_table(_view(), use_color=False, use_links=True)
    sprint_off = render.sprint_table(_view(), use_color=True)
    owner_on = render.owner_tree(owner_sections(), use_color=False, use_links=True)
    owner_off = render.owner_tree(owner_sections(), use_color=True)
    assert "\033]8;;" in sprint_on and "\033]8;;" in owner_on
    assert "\033]8;;" not in sprint_off and "\033]8;;" not in owner_off


def test_pr_marker_beside_health_glyph() -> None:
    from plate.issues.model import PR_DRAFT, PR_MERGED

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
    from plate.issues.model import PR_CLOSED, PR_MERGED, PR_OPEN

    merged = render._pr_marker(PR_MERGED, use_color=True)
    closed = render._pr_marker(PR_CLOSED, use_color=True)
    assert render.SOFT_GREEN in merged  # merged ⇄ tinted green
    assert render.SOFT_ROSE in closed  # closed ✗ tinted red
    assert render.PR_GLYPHS[PR_MERGED][0] == render.PR_GLYPHS[PR_OPEN][0]  # same glyph


def test_pr_marker_shown_in_markdown_with_number() -> None:
    from plate.issues.model import PR_OPEN

    row = make_row(5, title="T", pr_state=PR_OPEN, pr_number=99)
    out = render.markdown_tree([TreeNode(row, 0)])
    assert f"{render.PR_GLYPHS[PR_OPEN][0]} #99" in out


def test_context_node_blank_age_owned_child_has_age() -> None:
    # Age is rendered for owned rows only; the un-owned epic's age cell is blank.
    lines = render.terminal_tree(sample_forest(), use_color=False).splitlines()
    epic_line = next(line for line in lines if "#10" in line)
    child_line = next(line for line in lines if "#11" in line)
    assert "7w" in child_line  # 50 days -> 7w on the owned child
    assert "7w" not in epic_line  # context epic carries no age


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


# --- sprint view -------------------------------------------------------------


def make_sprint_row(
    number: int,
    *,
    title: str = "Title",
    assignees: list[str] | None = None,
    status: str | None = "Backlog",
    is_mine: bool = False,
    is_unassigned: bool = False,
    age_days: int = 3,
    is_stale: bool = False,
    labels: list[str] | None = None,
    labels_hidden: int = 0,
    comments: int = 0,
    sub_total: int = 0,
    sub_completed: int = 0,
    pr_state: str | None = None,
    pr_number: int | None = None,
) -> SprintRow:
    return SprintRow(
        number=number,
        url=f"https://github.com/an-org/a-repo/issues/{number}",
        title=title,
        labels=labels or [],
        labels_hidden=labels_hidden,
        comments_count=comments,
        age_days=age_days,
        is_stale=is_stale,
        sub_total=sub_total,
        sub_completed=sub_completed,
        assignees=assignees or [],
        status=status,
        is_mine=is_mine,
        is_unassigned=is_unassigned,
        pr_state=pr_state,
        pr_number=pr_number,
    )


def _view() -> SprintView:
    return SprintView(
        title="Sprint 7",
        yours=[
            make_sprint_row(
                3,
                title="Mine",
                assignees=["me"],
                is_mine=True,
                status="🚀 Shipping",
                pr_state="open",
                pr_number=99,
            )
        ],
        others=[
            make_sprint_row(
                2, title="Theirs", assignees=["a-teammate"], status="🛠 Building"
            )
        ],
        unassigned=[make_sprint_row(1, title="Free", is_unassigned=True)],
    )


def test_strip_emoji_keeps_words() -> None:
    assert render.strip_emoji("🚀 Shipping") == "Shipping"
    assert render.strip_emoji("Backlog") == "Backlog"


def test_sprint_table_has_title_and_three_dividers() -> None:
    out = render.sprint_table(_view(), use_color=False)
    assert "Sprint 7  ·  current sprint" in out
    for label in ("── yours (1)", "── others (1)", "── unassigned (1)"):
        assert label in out
    # title divider precedes the yours divider
    assert out.index("current sprint") < out.index("── yours")


def test_sprint_table_has_assignee_and_stripped_status() -> None:
    out = render.sprint_table(_view(), use_color=False)
    assert "Assignee" in out and "Status" in out
    assert "a-teammate" in out
    assert "Shipping" in out and "Building" in out
    assert "🚀" not in out and "🛠" not in out  # emoji stripped in terminal


def test_sprint_table_plain_when_color_off() -> None:
    out = render.sprint_table(_view(), use_color=False)
    assert "\033[" not in out  # no ANSI when colour disabled


def test_sprint_table_hides_hidden_label_on_others_row() -> None:
    resolver = {"wontfix": "hide"}.get
    view = SprintView(
        title="S",
        yours=[],
        others=[
            make_sprint_row(
                2, title="Theirs", assignees=["a-teammate"], labels=["wontfix", "bug"]
            )
        ],
        unassigned=[],
    )
    out = render.sprint_table(view, use_color=False, resolver=resolver)
    assert "wontfix" not in out
    assert "bug" in out


def test_sprint_table_others_row_colour_label_not_promoted() -> None:
    # colour/promotion stays mine-only: an "alert"-styled label on an others
    # row is packed plainly like any other label, then dimmed with the row.
    resolver = {"blocked": "alert"}.get
    view = SprintView(
        title="S",
        yours=[],
        others=[
            make_sprint_row(
                2, title="Theirs", assignees=["a-teammate"], labels=["bug", "blocked"]
            )
        ],
        unassigned=[],
    )
    out = render.sprint_table(view, use_color=True, resolver=resolver)
    assert render.SOFT_ROSE not in out  # no colour promotion on others rows
    visible = render.visible_text(out)
    assert "bug" in visible and "blocked" in visible


def test_sprint_markdown_sections_and_emoji_kept() -> None:
    out = render.sprint_markdown(_view())
    assert "## Sprint 7 · current sprint" in out
    assert "### yours (1)" in out and "### others (1)" in out
    assert "### unassigned (1)" in out
    assert "[#3](https://github.com/an-org/a-repo/issues/3)" in out
    assert "@a-teammate" in out
    assert "🚀 Shipping" in out  # markdown keeps the emoji
    assert "⇄ #99 open" in out  # PR marker rendered


def test_sprint_markdown_marks_empty_bucket() -> None:
    view = SprintView(
        title="S",
        yours=[],
        others=[],
        unassigned=[make_sprint_row(1, is_unassigned=True)],
    )
    out = render.sprint_markdown(view)
    assert "### yours (0)\n- *none*" in out


def test_sprint_key_names_bucket_order_and_others_glyph() -> None:
    out = render.sprint_key(use_color=False)
    assert "Key" in out
    assert "yours" in out and "others" in out and "unassigned" in out
    assert "yours -> others -> unassigned" in out
    assert "someone else's / unassigned row" in out
    assert "parent not assigned to you" not in out


def test_sprint_key_plain_when_color_off() -> None:
    out = render.sprint_key(use_color=False)
    assert "\033[" not in out


# --- owner-wide view ---------------------------------------------------------


def owner_sections() -> list[tuple[str, list[TreeNode]]]:
    # repo-x: a context epic over a mine row, an others row, and an unassigned
    # row; repo-y: a single standalone mine row.
    mine = TreeNode(
        make_row(
            11,
            title="Mine task",
            mine=True,
            context=False,
            assignees=["me"],
            age_days=40,
            is_stale=True,
        ),
        depth=1,
    )
    others = TreeNode(
        make_row(
            12,
            title="Theirs",
            mine=False,
            context=False,
            assignees=["alice"],
            age_days=5,
            is_stale=False,
        ),
        depth=1,
    )
    free = TreeNode(
        make_row(
            13,
            title="Free task",
            mine=False,
            context=False,
            assignees=[],
            age_days=3,
            is_stale=False,
        ),
        depth=1,
    )
    epic = TreeNode(
        make_row(
            10, title="Epic", mine=False, context=True, sub_total=4, sub_completed=1
        ),
        depth=0,
        children=[mine, others, free],
    )
    standalone = TreeNode(
        make_row(
            20,
            repo="an-org/repo-y",
            title="Solo",
            mine=True,
            assignees=["me"],
            age_days=2,
            is_stale=False,
        ),
        depth=0,
    )
    return [("an-org/repo-x", [epic]), ("an-org/repo-y", [standalone])]


def test_owner_tree_sections_in_order_with_open_counts() -> None:
    out = render.owner_tree(owner_sections(), use_color=False)
    assert out.index("an-org/repo-x") < out.index("an-org/repo-y")  # order kept
    # divider carries repo name + count of non-context (real, open) rows
    assert "── an-org/repo-x · 3 open" in out
    assert "── an-org/repo-y · 1 open" in out


def test_owner_tree_row_weights_by_class() -> None:
    lines = render.owner_tree(owner_sections(), use_color=False).splitlines()
    mine_line = next(line for line in lines if "#11" in line)
    others_line = next(line for line in lines if "#12" in line)
    free_line = next(line for line in lines if "#13" in line)
    epic_line = next(line for line in lines if "#10" in line)
    assert "•" in mine_line  # mine, stale -> health glyph
    assert "alice" in others_line  # others -> assignee shown
    assert "✓" in free_line and "—" in free_line  # unassigned: glyph + em dash
    # context ancestor: neutral glyph, rollup, no health glyph / assignee
    assert render.CONTEXT_GLYPH + "   #10" in epic_line
    assert "1/4" in epic_line


def test_owner_tree_others_row_dimmed_whole() -> None:
    out = render.owner_tree(owner_sections(), use_color=True)
    others_line = next(line for line in out.splitlines() if "#12" in line)
    assert others_line.startswith(render.DIM)  # whole line dimmed
    assert "alice" in render.visible_text(others_line)


def test_owner_tree_indents_children_under_section() -> None:
    lines = render.owner_tree(owner_sections(), use_color=False).splitlines()
    epic_line = next(line for line in lines if "#10" in line)
    child_line = next(line for line in lines if "#11" in line)
    assert child_line.index("#11") > epic_line.index("#10")


def test_owner_markdown_headings_nesting_and_assignee_meta() -> None:
    out = render.owner_markdown(owner_sections())
    lines = out.splitlines()
    assert "## an-org/repo-x" in out and "## an-org/repo-y" in out
    # nested: the mine child is indented under the epic
    assert any(line.startswith("  - ") and "#11" in line for line in lines)
    # @login only on the others row — not mine, not unassigned
    others_line = next(line for line in lines if "#12" in line)
    mine_line = next(line for line in lines if "#11" in line)
    free_line = next(line for line in lines if "#13" in line)
    assert "@alice" in others_line
    assert "@" not in mine_line and "@" not in free_line


def test_owner_key_mentions_dual_glyph_meaning() -> None:
    out = render.owner_key(use_color=False)
    assert "Key" in out
    assert "someone else's issue" in out and "structure" in out
    assert "grouped by repository" in out


def test_owner_key_plain_when_color_off() -> None:
    assert "\033[" not in render.owner_key(use_color=False)


# --- untrusted text is sanitised before it reaches the terminal ------------------


def test_compact_text_neutralises_control_characters() -> None:
    from plate.core.render import compact_text

    hostile = (
        "Fix login \x1b]8;;https://evil.example/x\x1b\\click\x1b]8;;\x1b\\ now\x07"
    )
    out = compact_text(hostile)
    assert out == "Fix login click now"
    assert compact_text("\x1b[31mbug\x1b[0m") == "bug"
    assert compact_text("tail\x1b") == "tail"  # lone ESC, no sequence to strip
    assert compact_text("a\x9b31mb") == "ab"  # 8-bit CSI introducer
    # C1 controls and DEL go the same way; ordinary whitespace still collapses.
    assert compact_text("a\x85b\x7fc\n\n  d\te") == "a b c d e"
    assert compact_text(None) == "" and compact_text(42) == ""
    # Emoji, ZWJ sequences and East Asian text are not control characters.
    assert compact_text("🚀 打开 👩‍💻") == "🚀 打开 👩‍💻"
