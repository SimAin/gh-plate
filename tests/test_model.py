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


# --- sprint view -------------------------------------------------------------


def make_item(
    number: int,
    *,
    repo: str = "an-org/a-repo",
    typename: str = "Issue",
    title: str = "Item",
    assignees: list[str] | None = None,
    status: str | None = "Backlog",
    iteration: str | None = "Sprint 7",
    updated_days_ago: int | None = 0,
    sub_total: int = 0,
    sub_completed: int = 0,
    prs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    content: dict[str, Any] = {"__typename": typename}
    if typename == "Issue":
        content.update(
            {
                "number": number,
                "title": title,
                "url": f"https://github.com/{repo}/issues/{number}",
                "updatedAt": _ts(updated_days_ago),
                "state": "OPEN",
                "repository": {"nameWithOwner": repo},
                "assignees": {"nodes": [{"login": a} for a in (assignees or [])]},
                "labels": {"nodes": []},
                "comments": {"totalCount": 0},
                "subIssuesSummary": {"total": sub_total, "completed": sub_completed},
                "parent": None,
                "closedByPullRequestsReferences": {"nodes": prs or []},
            }
        )
    item: dict[str, Any] = {"content": content}
    item["status"] = {"name": status} if status is not None else None
    item["iteration"] = {"title": iteration} if iteration is not None else None
    return item


def sprint(items: list[dict[str, Any]], *, login: str = "me", **kwargs: Any):
    return model.build_sprint_view(
        items,
        login=login,
        repo=kwargs.pop("repo", "an-org/a-repo"),
        now=NOW,
        stale_days=kwargs.pop("stale_days", 14),
        status_order=kwargs.pop("status_order", ()),
    )


def test_sprint_buckets_by_assignee() -> None:
    view = sprint(
        [
            make_item(1, assignees=["me"]),
            make_item(2, assignees=["other"]),
            make_item(3, assignees=[]),
            make_item(4, assignees=["other", "me"]),  # multi-assignee incl. me
        ]
    )
    assert [r.number for r in view.yours] == [4, 1]   # both mine, by number desc
    assert [r.number for r in view.others] == [2]
    assert [r.number for r in view.unassigned] == [3]


def test_strip_emoji_keeps_words() -> None:
    assert model.strip_emoji("🚀 Shipping") == "Shipping"
    assert model.strip_emoji("Backlog") == "Backlog"


def test_normalize_status_strips_emoji_and_casefolds() -> None:
    assert model.normalize_status("🚀 Priority") == "priority"
    assert model.normalize_status("PRIORITY") == "priority"
    assert model.normalize_status("priority") == "priority"


def test_status_rank_matches_configured_order_after_emoji_strip_and_casefold() -> None:
    # A user configures statusOrder as displayed ("Priority"); the raw board
    # name carries an emoji ("🚀 Priority") and different casing shows up too —
    # both must resolve to the same rank as an exact match would.
    order = ["Priority", "Backlog"]
    assert model.status_rank("🚀 Priority", order) == 0
    assert model.status_rank("PRIORITY", order) == 0
    assert model.status_rank("priority", order) == 0
    assert model.status_rank("Backlog", order) == 1
    assert model.status_rank("Mystery", order) == len(order)
    assert model.status_rank(None, order) == len(order)


def test_sprint_sorts_active_first_by_status_order() -> None:
    order = ["In progress", "In review", "Backlog"]
    view = sprint(
        [
            make_item(1, assignees=["me"], status="Backlog"),
            make_item(2, assignees=["me"], status="In progress"),
            make_item(3, assignees=["me"], status="In review"),
            make_item(4, assignees=["me"], status="Mystery"),  # unlisted -> last
        ],
        status_order=order,
    )
    assert [r.number for r in view.yours] == [2, 3, 1, 4]


def test_sprint_sorts_active_first_when_status_order_configured_as_displayed() -> None:
    # statusOrder is configured against what the terminal shows ("Priority"),
    # while the board's raw Status value still carries the emoji ("🚀 Priority").
    view = sprint(
        [
            make_item(1, assignees=["me"], status="Backlog"),
            make_item(2, assignees=["me"], status="🚀 Priority"),
        ],
        status_order=["Priority", "Backlog"],
    )
    assert [r.number for r in view.yours] == [2, 1]
    assert view.yours[0].status == "🚀 Priority"  # raw status preserved on the row


def test_sprint_drops_other_repos_and_non_issues() -> None:
    view = sprint(
        [
            make_item(1, assignees=["me"]),
            make_item(2, assignees=["me"], repo="an-org/other"),  # wrong repo
            make_item(3, typename="PullRequest"),                  # not an issue
        ]
    )
    assert [r.number for r in view.yours] == [1]
    assert view.others == [] and view.unassigned == []


def test_sprint_title_from_first_item_and_status_preserved() -> None:
    view = sprint([make_item(1, assignees=["me"], status="🚀 Shipping")])
    assert view.title == "Sprint 7"
    assert view.yours[0].status == "🚀 Shipping"   # raw, emoji kept


def test_sprint_title_survives_repo_filter() -> None:
    # An active sprint whose only items are in another repo: no rows, but the
    # title is still known (so the CLI can say "empty sprint", not "no sprint").
    view = sprint([make_item(1, assignees=["me"], repo="an-org/other")])
    assert view.is_empty
    assert view.title == "Sprint 7"


def test_sprint_no_active_sprint_has_no_title() -> None:
    # No active iteration: the board's @current filter returns nothing at all.
    view = sprint([])
    assert view.is_empty
    assert view.title is None


def test_sprint_drops_items_missing_iteration_value() -> None:
    # Belt-and-braces (#2): an item without an iteration value wasn't matched by
    # the ``@current`` filter, so it must not render as the current sprint.
    view = sprint(
        [
            make_item(1, assignees=["me"], iteration="Sprint 7"),
            make_item(2, assignees=["me"], iteration=None),
        ]
    )
    assert [r.number for r in view.yours] == [1]


def test_sprint_empty_when_no_matching_items() -> None:
    view = sprint([make_item(1, typename="PullRequest")])
    assert view.is_empty
