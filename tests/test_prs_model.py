"""Tests for plate.prs.model — the pure PR domain layer.

Ported from gh-pr-status's tests/test_cli.py, keeping the model-level
assertions (bot detection, state priority, review/check mapping, grouping
flags, age/staleness, comment count, summary counts). Render-only assertions
(colour, terminal/markdown layout) belong with the render sub-issue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from plate.prs import model


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


# A real release-please title for this repo (see model._RELEASE_PR_RE).
RELEASE_TITLE = "chore(main): release 0.5.0"


def test_normalizes_flags_comments_and_release() -> None:
    rows = model.normalize_rows(
        [
            pr(1, "Mine", ["simon"], comments=2),
            pr(2, "Needs review", ["alice"], review_decision=None, comments=3),
            pr(3, RELEASE_TITLE, review_decision="APPROVED"),
        ],
        "simon",
    )

    assert rows[0].is_mine
    assert not rows[0].is_to_review
    assert rows[0].comments_count == 2
    assert not rows[1].is_mine
    assert rows[1].is_to_review
    assert rows[1].comments_count == 3
    assert rows[2].is_release_pr


def test_authored_pr_is_mine_even_without_assignee() -> None:
    rows = model.normalize_rows(
        [
            pr(1, "Authored, unassigned", author="simon"),
            pr(2, "Someone else's, unassigned", author="alice"),
        ],
        "simon",
    )

    assert rows[0].is_mine
    assert not rows[0].is_to_review
    assert not rows[1].is_mine
    assert rows[1].is_to_review


def test_sort_priority() -> None:
    rows = model.normalize_rows(
        [
            pr(1, "Assigned to someone", ["alice"], review_decision="APPROVED"),
            pr(2, "Unassigned approved", review_decision="APPROVED"),
            pr(3, "To review", ["alice"]),
            pr(4, "Mine", ["simon"]),
        ],
        "simon",
    )

    sorted_numbers = [row.number for row in sorted(rows, key=model.sort_key)]
    assert sorted_numbers == [4, 3, 1, 2]


def test_release_pr_detection_matches_version_suffix_not_ordinary_prs() -> None:
    # The chosen pattern must catch release-please's scoped, version-suffixed
    # title and the older literal "chore: release main", while leaving ordinary
    # chore PRs alone.
    assert model.is_release_pr("chore(main): release 0.5.0")
    assert model.is_release_pr("chore(main): release 1.2.3")
    assert model.is_release_pr("chore: release main")
    assert not model.is_release_pr("chore: update dependencies")
    assert not model.is_release_pr("feat: release the changelog tooling")
    assert not model.is_release_pr(None)


def test_me_substitution_in_assignees() -> None:
    rows = model.normalize_rows(
        [
            pr(1, "Just me", ["simon"]),
            pr(2, "Shared", ["simon", "alice"], author="simon"),
            pr(3, "Nobody", []),
        ],
        "simon",
    )

    assert rows[0].assignees == ["me"]
    assert rows[1].assignees == ["me", "alice"]
    assert rows[2].assignees == []


def test_pr_state_priority() -> None:
    def state(**kwargs: Any) -> str:
        row = model.normalize_rows([pr(1, "x", ["simon"], **kwargs)], "simon")[0]
        return model.pr_state(row)

    assert state(is_draft=True) == "draft"
    assert state(mergeable="CONFLICTING") == "conflict"
    assert state(review_decision="APPROVED", rollup="SUCCESS") == "ready"
    assert state() == "waiting"
    # UNKNOWN mergeability must not upgrade to "ready" — GitHub is still
    # recomputing, so the merge-this-now claim is unconfirmed.
    assert (
        state(review_decision="APPROVED", rollup="SUCCESS", mergeable="UNKNOWN")
        == "unknown"
    )
    # For a PR that is waiting anyway, UNKNOWN changes nothing.
    assert state(mergeable="UNKNOWN") == "waiting"
    # Failing CI / requested changes are shown in their columns, not as a
    # headline state.
    assert state(rollup="FAILURE") == "waiting"
    assert state(review_decision="CHANGES_REQUESTED") == "waiting"


def test_review_status_mapping() -> None:
    def status(decision: str | None) -> str:
        return model.normalize_rows([pr(1, "x", review_decision=decision)], "s")[
            0
        ].review_status

    assert status("APPROVED") == "approved"
    assert status("CHANGES_REQUESTED") == "pending (changes requested)"
    assert status(None) == "pending"


def test_i_approved_from_latest_reviews() -> None:
    rows = model.normalize_rows(
        [
            pr(
                1,
                "I reviewed someone else's PR",
                ["alice"],
                review_decision=None,
                latest_reviews=[review("simon", "APPROVED")],
            ),
        ],
        "simon",
    )

    assert rows[0].i_approved
    assert not rows[0].is_mine


def test_age_and_staleness() -> None:
    now = datetime(2026, 6, 19, tzinfo=UTC)
    fresh = (now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    old = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    rows = model.normalize_rows(
        [
            pr(1, "Fresh", ["alice"], updated_at=fresh),
            pr(2, "Stale", ["alice"], updated_at=old),
        ],
        "simon",
        now=now,
        stale_days=14,
    )

    assert rows[0].age_days == 2
    assert not rows[0].is_stale
    assert rows[1].age_days == 30
    assert rows[1].is_stale


def test_bot_detection_matrix() -> None:
    rows = model.normalize_rows(
        [
            pr(1, "Bump lib from 1.0 to 1.1", author="app/dependabot"),
            pr(2, "Update all deps", author="app/renovate"),
            pr(3, "Auto-format", author="github-actions[bot]"),
            pr(4, "Typed as Bot", author="some-ci", author_type="Bot"),
            pr(5, "Human, not a bot", author="dependabotfan"),
        ],
        "simon",
    )

    assert rows[0].bot_name == "dependabot"
    assert rows[1].bot_name == "renovate"
    assert rows[2].bot_name == "github-actions"
    assert rows[3].bot_name == "some-ci"
    assert rows[4].bot_name is None


def test_check_state_follows_githubs_rollup() -> None:
    # GitHub's rollup state on the head commit is the verdict the UI shows;
    # the model maps it 1:1 instead of re-aggregating checks.
    cases = {
        "SUCCESS": "success",
        "FAILURE": "failure",
        "ERROR": "failure",
        "PENDING": "pending",
        "EXPECTED": "pending",
        None: "none",
    }
    for rollup, expected in cases.items():
        rows = model.normalize_rows([pr(1, "x", rollup=rollup)], "simon")
        assert rows[0].check_state == expected


def test_conflict_flag() -> None:
    rows = model.normalize_rows(
        [
            pr(1, "Clean", ["alice"], mergeable="MERGEABLE"),
            pr(2, "Conflicting", ["alice"], mergeable="CONFLICTING"),
        ],
        "simon",
    )

    assert not rows[0].has_conflicts
    assert rows[1].has_conflicts
    assert model.pr_state(rows[1]) == "conflict"


def test_grouping_flags() -> None:
    rows = model.normalize_rows(
        [
            pr(1, "Mine", ["simon"]),
            pr(2, "To review", ["alice"]),
            pr(3, "Someone else's, settled", ["bob"], review_decision="APPROVED"),
        ],
        "simon",
    )

    assert model.sort_group(rows[0]) == 0
    assert model.sort_group(rows[1]) == 1
    assert model.sort_group(rows[2]) == 2


def test_summary_counts() -> None:
    rows = model.normalize_rows(
        [
            pr(1, "Mine", ["simon"]),
            pr(2, "To review", ["alice"]),
            pr(3, "Conflicting", ["alice"], mergeable="CONFLICTING"),
            pr(4, "Failing", ["bob"], review_decision="APPROVED", rollup="FAILURE"),
        ],
        "simon",
    )

    counts = model.summary_counts(rows)
    assert counts.open == 4
    assert counts.to_review == 2
    assert counts.conflicts == 1
    assert counts.failing_ci == 1


def test_summary_counts_zeroes_when_nothing_pending() -> None:
    rows = model.normalize_rows([pr(1, "Mine", ["simon"])], "simon")
    counts = model.summary_counts(rows)
    assert counts == model.PrSummary(open=1, to_review=0, conflicts=0, failing_ci=0)


def test_comment_count_is_the_badge_figure() -> None:
    rows = model.normalize_rows([pr(1, "Mine", ["simon"], comments=9)], "simon")
    assert rows[0].comments_count == 9


# --- repo field (owner view) --------------------------------------------------


def _with_repo(node: dict[str, object], repo: str) -> dict[str, object]:
    """An owner-search node: the PR payload plus its own repository field."""
    return {**node, "repository": {"nameWithOwner": repo}}


def test_repo_read_from_nodes_own_repository_field() -> None:
    rows = model.normalize_rows(
        [_with_repo(pr(1, "Owner-search node"), "acme/widget")], "simon"
    )
    assert rows[0].repo == "acme/widget"


def test_repo_falls_back_to_the_queried_repo() -> None:
    # Repo-view nodes carry no repository field; the call site passes the repo.
    rows = model.normalize_rows([pr(1, "Repo-view node")], "simon", repo="acme/widget")
    assert rows[0].repo == "acme/widget"


def test_nodes_own_repository_wins_over_fallback() -> None:
    rows = model.normalize_rows(
        [_with_repo(pr(1, "x"), "acme/other")], "simon", repo="acme/widget"
    )
    assert rows[0].repo == "acme/other"


# --- group_by_repo ------------------------------------------------------------


def test_group_by_repo_sections_in_first_appearance_order() -> None:
    # Fetch order is sort:updated-desc, so first appearance = most recently
    # active; the grouping must preserve exactly that order across repos.
    rows = model.normalize_rows(
        [
            _with_repo(pr(5, "freshest"), "acme/repo-b"),
            _with_repo(pr(3, "older"), "acme/repo-a"),
            _with_repo(pr(4, "old too"), "acme/repo-b"),
            _with_repo(pr(1, "oldest"), "acme/repo-c"),
        ],
        "simon",
    )
    sections = model.group_by_repo(rows)
    assert [repo for repo, _ in sections] == [
        "acme/repo-b", "acme/repo-a", "acme/repo-c"
    ]


def test_group_by_repo_rows_keep_fetch_order_within_a_repo() -> None:
    rows = model.normalize_rows(
        [
            _with_repo(pr(5, "first fetched"), "acme/repo-b"),
            _with_repo(pr(3, "elsewhere"), "acme/repo-a"),
            _with_repo(pr(9, "second fetched"), "acme/repo-b"),
        ],
        "simon",
    )
    sections = dict(model.group_by_repo(rows))
    assert [row.number for row in sections["acme/repo-b"]] == [5, 9]


def test_group_by_repo_empty_rows_yield_no_sections() -> None:
    assert model.group_by_repo([]) == []
