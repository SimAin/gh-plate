"""Tests for plate.prs.github (PR-domain GraphQL fetches).

Ported from gh-pr-status's parsing/merging assertions onto
``plate.core.gh.run_command`` — the shared subprocess chokepoint every domain
fetch calls through, monkeypatched here exactly as ``tests/test_github.py``
does for ``plate.issues.github``. No live ``gh`` calls.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from plate.core import gh
from plate.prs import github


def pr_node(number: int, title: str = "") -> dict[str, Any]:
    """A minimal PR node in the GraphQL shape returned by PR_QUERY."""
    return {"number": number, "title": title or f"PR {number}"}


def page(
    viewer: str | None, numbers: list[int], *, include_repository: bool = True
) -> str:
    data: dict[str, Any] = {}
    if viewer is not None:
        data["viewer"] = {"login": viewer}
    if include_repository:
        data["repository"] = {"pullRequests": {"nodes": [pr_node(n) for n in numbers]}}
    return json.dumps({"data": data})


# --- parse_graphql_documents --------------------------------------------------


def test_parse_graphql_documents_splits_concatenated_pages() -> None:
    text = page("simon", [1, 2]) + page("simon", [3])
    documents = github.parse_graphql_documents(text)
    assert len(documents) == 2


def test_parse_graphql_documents_tolerates_whitespace_between_pages() -> None:
    text = page("simon", [1]) + "\n\n  " + page("simon", [2]) + "\n"
    documents = github.parse_graphql_documents(text)
    assert len(documents) == 2


def test_parse_graphql_documents_ignores_non_dict_documents() -> None:
    text = "[1, 2, 3]" + page("simon", [1])
    documents = github.parse_graphql_documents(text)
    assert len(documents) == 1


def test_parse_graphql_documents_empty_text_yields_no_documents() -> None:
    assert github.parse_graphql_documents("") == []
    assert github.parse_graphql_documents("   \n  ") == []


def test_parse_graphql_documents_raises_value_error_on_malformed_json() -> None:
    with pytest.raises(ValueError):
        github.parse_graphql_documents("{not json")


# --- merge_graphql_pages -------------------------------------------------------


def test_merge_graphql_pages_combines_nodes_and_extracts_viewer() -> None:
    documents = [
        json.loads(page("simon", [1, 2])),
        json.loads(page("simon", [3])),
    ]
    viewer, prs = github.merge_graphql_pages(documents)
    assert viewer == "simon"
    assert [p["number"] for p in prs] == [1, 2, 3]


def test_merge_graphql_pages_keeps_last_seen_viewer() -> None:
    # viewer is fetched on every page identically; the last non-empty login
    # wins if pages ever disagreed (they shouldn't in practice).
    documents = [json.loads(page("simon", [1])), json.loads(page("simon", [2]))]
    viewer, _ = github.merge_graphql_pages(documents)
    assert viewer == "simon"


def test_merge_graphql_pages_viewer_none_when_never_present() -> None:
    documents = [json.loads(page(None, [1]))]
    viewer, prs = github.merge_graphql_pages(documents)
    assert viewer is None
    assert [p["number"] for p in prs] == [1]


def test_merge_graphql_pages_tolerates_missing_repository() -> None:
    documents = [json.loads(page("simon", [], include_repository=False))]
    viewer, prs = github.merge_graphql_pages(documents)
    assert viewer == "simon"
    assert prs == []


def test_merge_graphql_pages_empty_documents_yields_empty_result() -> None:
    assert github.merge_graphql_pages([]) == (None, [])


# --- fetch_prs_and_viewer ------------------------------------------------------


def _fake_run_with_stdout(stdout: str, returncode: int = 0) -> Any:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, returncode, stdout=stdout, stderr="" if returncode == 0 else stdout
        )

    return fake_run


def test_fetch_prs_and_viewer_single_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout(page("simon", [1, 2])))
    viewer, prs = github.fetch_prs_and_viewer("an-org/a-repo", 10)
    assert viewer == "simon"
    assert [p["number"] for p in prs] == [1, 2]


def test_fetch_prs_and_viewer_slices_to_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gh, "run_command", _fake_run_with_stdout(page("simon", [1, 2, 3]))
    )
    _viewer, prs = github.fetch_prs_and_viewer("an-org/a-repo", 2)
    assert [p["number"] for p in prs] == [1, 2]


def test_fetch_prs_and_viewer_rejects_repo_without_slash() -> None:
    with pytest.raises(gh.PlateError, match="OWNER/REPO"):
        github.fetch_prs_and_viewer("not-a-valid-repo", 10)


def test_fetch_prs_and_viewer_gh_failure_raises_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(gh.PlateError, match="boom"):
        github.fetch_prs_and_viewer("an-org/a-repo", 10)


def test_fetch_prs_and_viewer_malformed_json_raises_plate_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout("{not json"))
    with pytest.raises(gh.PlateError, match="Could not parse"):
        github.fetch_prs_and_viewer("an-org/a-repo", 10)


def test_fetch_prs_and_viewer_page_size_capped_at_100(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout=page("simon", []), stderr="")

    monkeypatch.setattr(gh, "run_command", fake_run)
    github.fetch_prs_and_viewer("an-org/a-repo", 500)

    assert "pageSize=100" in captured["args"]
    assert "--paginate" in captured["args"]


def test_fetch_prs_and_viewer_omits_paginate_when_limit_fits_one_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout=page("simon", []), stderr="")

    monkeypatch.setattr(gh, "run_command", fake_run)
    github.fetch_prs_and_viewer("an-org/a-repo", 10)

    assert "pageSize=10" in captured["args"]
    assert "--paginate" not in captured["args"]


def test_fetch_prs_and_viewer_passes_owner_and_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout=page("simon", []), stderr="")

    monkeypatch.setattr(gh, "run_command", fake_run)
    github.fetch_prs_and_viewer("an-org/a-repo", 10)

    assert "owner=an-org" in captured["args"]
    assert "name=a-repo" in captured["args"]


# --- owner_search_query --------------------------------------------------------


@pytest.mark.parametrize(
    "owner_type,qualifier",
    [("organization", "org"), ("user", "user")],
)
def test_owner_search_query_uses_owner_type_qualifier(
    owner_type: str, qualifier: str
) -> None:
    query = github.owner_search_query("acme", owner_type, "alice", mine=False)
    assert query.startswith(f"{qualifier}:acme ")
    assert "author:" not in query


@pytest.mark.parametrize("owner_type", ["organization", "user"])
def test_owner_search_query_always_excludes_archived_and_sorts(
    owner_type: str,
) -> None:
    query = github.owner_search_query("acme", owner_type, "alice", mine=False)
    assert "archived:false" in query
    assert "sort:updated-desc" in query
    assert "is:pr" in query
    assert "is:open" in query


def test_owner_search_query_mine_appends_author_filter() -> None:
    # --mine means authored-by-you (DECISIONS.md D9), not involves/assignee.
    query = github.owner_search_query("acme", "organization", "alice", mine=True)
    assert query.endswith("author:alice")
    assert "involves:" not in query
    assert "assignee:" not in query


def test_owner_search_query_mine_false_omits_author_filter() -> None:
    query = github.owner_search_query("acme", "organization", "alice", mine=False)
    assert "author" not in query


# --- fetch_owner_prs -----------------------------------------------------------


def _search_payload(
    issue_count: int,
    nodes: list[dict[str, Any]],
    *,
    has_next: bool = False,
    end_cursor: str | None = None,
) -> str:
    return json.dumps(
        {
            "data": {
                "search": {
                    "issueCount": issue_count,
                    "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                    "nodes": nodes,
                }
            }
        }
    )


def test_fetch_owner_prs_issues_the_expected_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(
            args, 0, stdout=_search_payload(0, []), stderr=""
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    github.fetch_owner_prs("acme", "organization", "alice", 10, mine=True)

    q_arg = next(a for a in captured["args"] if a.startswith("q="))
    expected = github.owner_search_query("acme", "organization", "alice", mine=True)
    assert q_arg == f"q={expected}"
    query_arg = next(a for a in captured["args"] if a.startswith("query="))
    assert query_arg == f"query={github.PR_OWNER_QUERY}"


def test_fetch_owner_prs_delegates_to_core_search_paginated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_search(
        query: str, query_str: str, limit: int, error_context: str
    ) -> tuple[list[dict[str, Any]], int]:
        captured.update(
            query=query, query_str=query_str, limit=limit, error_context=error_context
        )
        return [{"number": 1}], 1

    monkeypatch.setattr(gh, "search_paginated", fake_search)
    prs, total = github.fetch_owner_prs("acme", "user", "alice", 7, mine=False)

    assert [p["number"] for p in prs] == [1]
    assert total == 1
    assert captured["query"] == github.PR_OWNER_QUERY
    assert captured["query_str"] == github.owner_search_query(
        "acme", "user", "alice", mine=False
    )
    assert captured["limit"] == 7
    assert captured["error_context"] == "acme"


def test_fetch_owner_prs_paginates_across_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        _search_payload(
            3, [{"number": 1}, {"number": 2}], has_next=True, end_cursor="CURSOR1"
        ),
        _search_payload(3, [{"number": 3}]),
    ]
    calls = {"n": 0}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        stdout = pages[calls["n"]]
        calls["n"] += 1
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(gh, "run_command", fake_run)
    prs, total = github.fetch_owner_prs("acme", "organization", "alice", 10, mine=False)
    assert [p["number"] for p in prs] == [1, 2, 3]
    assert total == 3
    assert calls["n"] == 2


def test_fetch_owner_prs_truncates_at_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=_search_payload(
                100,
                [{"number": i} for i in range(1, 11)],
                has_next=True,
                end_cursor="CURSOR",
            ),
            stderr="",
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    prs, total = github.fetch_owner_prs("acme", "organization", "alice", 5, mine=False)
    assert len(prs) == 5
    assert total == 100  # server total can exceed what limit lets through


def test_fetch_owner_prs_gh_failure_raises_with_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(gh.PlateError, match="acme"):
        github.fetch_owner_prs("acme", "organization", "alice", 5, mine=False)


def test_pr_owner_query_carries_repository_and_pr_fields() -> None:
    # The owner search spans repos, so every node must carry its own repo
    # identity; the rest of the fields are exactly what the repo view reads.
    assert "repository { nameWithOwner }" in github.PR_OWNER_QUERY
    assert "... on PullRequest" in github.PR_OWNER_QUERY
    for field in (
        "isDraft",
        "mergeable",
        "totalCommentsCount",
        "reviewDecision",
        "latestOpinionatedReviews",
        "reviewRequests",
        "statusCheckRollup",
    ):
        assert field in github.PR_OWNER_QUERY


# --- timeline query variant -----------------------------------------------------


def test_plain_query_carries_no_timeline_fields() -> None:
    assert "timelineItems" not in github.PR_QUERY
    assert "__EXTRA_FIELDS__" not in github.PR_QUERY


def test_timeline_query_only_adds_the_events_connection() -> None:
    assert "timelineItems" in github.PR_TIMELINE_QUERY
    assert "__EXTRA_FIELDS__" not in github.PR_TIMELINE_QUERY
    stripped = github.PR_TIMELINE_QUERY.replace(github.TIMELINE_FIELDS + "\n", "")
    assert stripped == github.PR_QUERY


def test_fetch_prs_and_viewer_timeline_flag_picks_the_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout=page("simon", []), stderr="")

    monkeypatch.setattr(gh, "run_command", fake_run)
    github.fetch_prs_and_viewer("an-org/a-repo", 10)
    assert not any("timelineItems" in arg for arg in captured["args"])
    github.fetch_prs_and_viewer("an-org/a-repo", 10, timeline=True)
    assert any("timelineItems" in arg for arg in captured["args"])
