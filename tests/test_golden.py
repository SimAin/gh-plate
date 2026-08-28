"""Golden full-output snapshots of the three terminal views.

The other render tests assert on substrings, which cannot see a column drift by
one; these compare a whole rendered view against a snapshot generated from the
code and then read line by line. A diff here is either a deliberate layout
change (regenerate the snapshot and eyeball it) or a regression.

Terminal width is pinned by patching each renderer's own ``terminal_width``
(both import it from :mod:`plate.core.render`, so patching core would not reach
them); the retro panel sizes itself from its content and takes no width. Colour
is off so the snapshots stay legible.

Fixtures come from the neighbouring test modules rather than a second set here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from test_prs_render import pr as pr_node
from test_render import sample_forest
from test_retro_render import DAYS, NOW, section

from plate.issues import render as issues_render
from plate.prs import model as prs_model
from plate.prs import render as prs_render
from plate.retro import render as retro_render

NOW_PR = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def width(monkeypatch: pytest.MonkeyPatch):
    """Pin the terminal width both width-aware renderers see."""

    def pin(columns: int) -> None:
        monkeypatch.setattr(issues_render, "terminal_width", lambda: columns)
        monkeypatch.setattr(prs_render, "terminal_width", lambda: columns)

    return pin


def pr_rows() -> list[prs_model.PrRow]:
    """One PR per group and per signal: approved, failing CI, a CJK title that
    only display-width truncation cuts correctly, a bot release PR, and an
    approved someone-else's PR for "the rest"."""
    nodes: list[dict[str, Any]] = [
        pr_node(
            41,
            "Add width-aware truncation",
            ["user"],
            review_decision="APPROVED",
            rollup="SUCCESS",
            created_at="2026-06-12T09:00:00Z",
            last_commit=("2026-06-18T09:00:00Z", "user"),
        ),
        pr_node(
            37,
            "Rework the sprint buckets",
            ["user"],
            rollup="FAILURE",
            created_at="2026-05-20T09:00:00Z",
            last_commit=("2026-06-05T09:00:00Z", "user"),
        ),
        pr_node(
            35,
            "重构面板布局与颜色",
            ["alice"],
            author="alice",
            comments=3,
            created_at="2026-06-16T09:00:00Z",
            last_comment=("2026-06-17T09:00:00Z", "alice"),
        ),
        pr_node(
            30,
            "chore(main): release 0.5.0",
            author="github-actions",
            author_type="Bot",
            rollup="SUCCESS",
            created_at="2026-06-19T06:00:00Z",
        ),
        pr_node(
            22,
            "Bump actions/checkout",
            ["bob"],
            author="bob",
            review_decision="APPROVED",
            rollup="SUCCESS",
            created_at="2026-04-02T09:00:00Z",
            last_review=("2026-06-10T09:00:00Z", "bob"),
        ),
    ]
    return prs_model.normalize_rows(
        nodes, "user", now=NOW_PR, stale_days=14, repo="acme/widget"
    )


ISSUES_YOURS_80 = """\
Issue                                         Age  Labels              Prog  Cmt
── yours (2) ───────────────────────────────────────────────────────────────────
·   #10 Epic                                                            1/4
  •   #11 Child task                           7w                              0
✓   #3 Standalone                              5d                              0"""

ISSUES_YOURS_120 = """\
Issue                                                                                 Age  Labels              Prog  Cmt
── yours (2) ───────────────────────────────────────────────────────────────────────────────────────────────────────────
·   #10 Epic                                                                                                    1/4
  •   #11 Child task                                                                   7w                              0
✓   #3 Standalone                                                                      5d                              0"""

PRS_REPO_80 = """\
   PR      Title             Assignee           Age  Last  Review         CI  Cmt
── yours (2) ────────────────────────────────────────────────────────────────────
✓  #41     Add width-aware…  me                  7d    1d  approved       ✓     0
•  #37     Rework the spri…  me                  4w    2w  pending        ✗     0
── to review (2) ────────────────────────────────────────────────────────────────
•  #35     重构面板布局与…   alice               3d    2d  pending              3
•  #30     chore(main): re…  Release PR          0d        pending        ✓     0
── the rest (1) ─────────────────────────────────────────────────────────────────
✓  #22     Bump actions/ch…  bob                2mo    9d  approved       ✓     0"""

PRS_REPO_120 = """\
   PR      Title                                               Assignee           Age  Last  Review         CI  Cmt
── yours (2) ──────────────────────────────────────────────────────────────────────────────────────────────────────
✓  #41     Add width-aware truncation                          me                  7d    1d  approved       ✓     0
•  #37     Rework the sprint buckets                           me                  4w    2w  pending        ✗     0
── to review (2) ──────────────────────────────────────────────────────────────────────────────────────────────────
•  #35     重构面板布局与颜色                                  alice               3d    2d  pending              3
•  #30     chore(main): release 0.5.0                          Release PR          0d        pending        ✓     0
── the rest (1) ───────────────────────────────────────────────────────────────────────────────────────────────────
✓  #22     Bump actions/checkout                               bob                2mo    9d  approved       ✓     0"""

RETRO_ONE_OWNER = """\
── acme · last 14 days ───────────────────────────────────────────────────
               S  S  M  T  W  T  F  S  S  M  T  W  T  F    Σ
   reviews     ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  2  ·  ·    2   last 2d ago
   commits     ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  2    2   today
   opened      ·  ·  ·  ·  ·  ·  ·  ·  2  ·  ·  ·  ·  ·    2   last 5d ago
   closed      ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·    0   none in 14d"""

RETRO_TWO_OWNERS = """\
── acme · last 14 days ───────────────────────────────────────────────────
               S  S  M  T  W  T  F  S  S  M  T  W  T  F    Σ
   reviews     ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  2  ·  ·    2   last 2d ago
   commits     ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  2    2   today
   opened      ·  ·  ·  ·  ·  ·  ·  ·  2  ·  ·  ·  ·  ·    2   last 5d ago
   closed      ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·    0   none in 14d
── user · last 14 days ───────────────────────────────────────────────────
               S  S  M  T  W  T  F  S  S  M  T  W  T  F    Σ
   reviews     ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·    0   none in 14d
   commits     ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  2    2   today
   opened      ·  ·  ·  ·  ·  ·  ·  ·  2  ·  ·  ·  ·  ·    2   last 5d ago
   closed      ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·    0   none in 14d"""


def test_issues_yours_tree_narrow(width) -> None:
    width(80)
    assert issues_render.terminal_tree(sample_forest(), use_color=False) == (
        ISSUES_YOURS_80
    )


def test_issues_yours_tree_wide(width) -> None:
    width(120)
    assert issues_render.terminal_tree(sample_forest(), use_color=False) == (
        ISSUES_YOURS_120
    )


def test_prs_repo_table_narrow(width) -> None:
    # 81 columns for an 80-column terminal is the documented clamp: Title never
    # goes below MIN_TITLE_WIDTH, the table overflows instead.
    width(80)
    assert prs_render.terminal_table(pr_rows(), use_color=False) == PRS_REPO_80


def test_prs_repo_table_wide(width) -> None:
    width(120)
    assert prs_render.terminal_table(pr_rows(), use_color=False) == PRS_REPO_120


def test_retro_panel_one_owner() -> None:
    assert retro_render.panel([section()], DAYS, NOW, use_color=False) == (
        RETRO_ONE_OWNER
    )


def test_retro_panel_two_owners() -> None:
    sections = [section("acme"), section("user", reviews_last=None)]
    assert retro_render.panel(sections, DAYS, NOW, use_color=False) == (
        RETRO_TWO_OWNERS
    )
