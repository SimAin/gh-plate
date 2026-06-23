"""Tests for issue_check.model — the pure domain/tree layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from issue_check import model

NOW = datetime(2026, 6, 22, tzinfo=UTC)


def _ts(days: int | None) -> str | None:
    if days is None:
        return None
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def make_issue(
    number: int,
    *,
    title: str = "Issue",
    updated_days_ago: int | None = 0,
    labels: list[str] | None = None,
    comments: int = 0,
    sub_total: int = 0,
    sub_completed: int = 0,
    parent: dict[str, Any] | None = None,
    prs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "number": number,
        "title": title,
        "url": f"https://github.com/an-org/a-repo/issues/{number}",
        "updatedAt": _ts(updated_days_ago),
        "labels": {"nodes": [{"name": name} for name in (labels or [])]},
        "comments": {"totalCount": comments},
        "subIssuesSummary": {"total": sub_total, "completed": sub_completed},
        "closedByPullRequestsReferences": {"nodes": prs or []},
    }
    if parent is not None:
        issue["parent"] = parent
    return issue


def index(
    issues: list[dict[str, Any]], stale_days: int = 14
) -> dict[int, model.IssueRow]:
    return model.build_index(issues, now=NOW, stale_days=stale_days)


# --- normalization -----------------------------------------------------------

def test_emoji_shortcodes_stripped_from_owned_labels() -> None:
    idx = index([make_issue(1, labels=[":cockroach: bug", ":scroll: epic"])])
    assert idx[1].labels == ["bug", "epic"]


def test_age_and_staleness() -> None:
    idx = index([make_issue(1, updated_days_ago=3), make_issue(2, updated_days_ago=40)])
    assert (idx[1].age_days, idx[1].is_stale) == (3, False)
    assert (idx[2].age_days, idx[2].is_stale) == (40, True)


def test_stale_days_threshold_is_inclusive() -> None:
    idx = index([make_issue(1, updated_days_ago=14)], stale_days=14)
    assert idx[1].is_stale is True


def test_missing_timestamp_is_not_stale() -> None:
    idx = index([make_issue(1, updated_days_ago=None)])
    assert idx[1].age_days is None
    assert idx[1].is_stale is False


def test_progress_text_only_when_children_present() -> None:
    idx = index([make_issue(1, sub_total=8, sub_completed=3), make_issue(2)])
    assert model.progress_text(idx[1]) == "3/8"
    assert model.progress_text(idx[2]) == ""


def test_issue_state_two_branches() -> None:
    idx = index([make_issue(1, updated_days_ago=1), make_issue(2, updated_days_ago=99)])
    assert model.issue_state(idx[1]) == model.ACTIVE
    assert model.issue_state(idx[2]) == model.STALE


# --- linked PRs ("fix in flight") --------------------------------------------

def _pr(number: int, state: str, *, draft: bool = False) -> dict[str, Any]:
    return {"number": number, "state": state, "isDraft": draft}


def test_dominant_pr_prefers_open_over_merged_over_closed() -> None:
    issue = make_issue(
        1,
        prs=[
            _pr(10, "CLOSED"),
            _pr(11, "MERGED"),
            _pr(12, "OPEN"),  # ready open beats both
        ],
    )
    assert model.dominant_pr(issue) == (model.PR_OPEN, 12)


def test_dominant_pr_open_draft_distinguished() -> None:
    issue = make_issue(1, prs=[_pr(10, "OPEN", draft=True)])
    assert model.dominant_pr(issue) == (model.PR_DRAFT, 10)


def test_dominant_pr_draft_beats_merged() -> None:
    # A live (if draft) fix is more interesting than one that already landed.
    issue = make_issue(1, prs=[_pr(11, "MERGED"), _pr(10, "OPEN", draft=True)])
    assert model.dominant_pr(issue) == (model.PR_DRAFT, 10)


def test_dominant_pr_none_when_unlinked() -> None:
    assert model.dominant_pr(make_issue(1)) == (None, None)


def test_pr_state_flows_to_owned_row_but_not_context() -> None:
    epic = make_issue(10, title="Epic")  # ancestor, no PRs of its own here
    idx = index([make_issue(11, parent=epic, prs=[_pr(20, "MERGED")])])
    assert idx[11].pr_state == model.PR_MERGED
    assert idx[11].pr_number == 20
    assert idx[10].pr_state is None  # context ancestor carries no PR marker


# --- index: owned issues + context ancestors ---------------------------------

def test_index_materializes_unowned_ancestor_as_context() -> None:
    epic = make_issue(10, title="Epic", sub_total=3, sub_completed=1)
    idx = index([make_issue(11, parent=epic)])
    assert set(idx) == {10, 11}
    assert idx[11].mine is True
    assert idx[10].mine is False          # ancestor not assigned to me
    assert idx[10].labels == []           # context nodes carry no labels
    assert idx[10].sub_total == 3         # but do carry their rollup
    assert idx[11].parent_number == 10


def test_owned_parent_is_not_overwritten_by_context() -> None:
    # #10 is both assigned to me AND the parent of #11 -> stays mine.
    epic_ctx = make_issue(10, title="Epic")
    idx = index(
        [make_issue(10, title="Epic", sub_total=2), make_issue(11, parent=epic_ctx)]
    )
    assert idx[10].mine is True
    assert idx[10].sub_total == 2


def test_two_level_ancestor_chain_is_walked() -> None:
    grandparent = make_issue(100, title="Program")
    parent = make_issue(10, title="Epic", parent=grandparent)
    idx = index([make_issue(11, parent=parent)])
    assert set(idx) == {11, 10, 100}
    assert idx[10].parent_number == 100
    assert idx[100].parent_number is None


# --- forest: shape and ordering ----------------------------------------------

def test_forest_roots_sorted_active_first() -> None:
    forest = model.build_forest(
        index([make_issue(3, updated_days_ago=5), make_issue(1, updated_days_ago=100)])
    )
    assert [n.row.number for n in forest] == [3, 1]


def test_group_floats_by_freshest_member_not_parent_age() -> None:
    # Context parent #10 is itself stale (40d) but its child #11 is fresh (1d);
    # a standalone #3 is 10d. The #10 group must outrank #3 via its fresh child.
    parent = make_issue(10, title="Epic", updated_days_ago=40)
    forest = model.build_forest(
        index(
            [
                make_issue(11, parent=parent, updated_days_ago=1),
                make_issue(3, updated_days_ago=10),
            ]
        )
    )
    assert [n.row.number for n in forest] == [10, 3]
    flat = [n.row.number for n in model.flatten(forest)]
    assert flat == [10, 11, 3]            # child nested under its parent
    assert forest[0].children[0].row.number == 11
    assert forest[0].depth == 0 and forest[0].children[0].depth == 1


def test_children_sorted_active_first_within_parent() -> None:
    parent = make_issue(10, title="Epic", updated_days_ago=5)
    forest = model.build_forest(
        index(
            [
                make_issue(11, parent=parent, updated_days_ago=2),
                make_issue(12, parent=parent, updated_days_ago=40),
            ]
        )
    )
    (root,) = forest
    assert [c.row.number for c in root.children] == [11, 12]


def test_subtree_of_only_undated_nodes_sorts_last() -> None:
    # A standalone dated issue (#3) must outrank a subtree with no timestamps.
    parent = make_issue(10, title="Epic", updated_days_ago=None)
    forest = model.build_forest(
        index(
            [
                make_issue(11, parent=parent, updated_days_ago=None),
                make_issue(3, updated_days_ago=100),
            ]
        )
    )
    assert [n.row.number for n in forest] == [3, 10]
