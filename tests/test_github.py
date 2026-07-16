"""Tests for issue_check.github.

Most of this module is pure URL/query building and is exercised directly —
those tests never shell out. The handful that reach an I/O boundary function
(``_search_issues``, ``fetch_owner_issues``, ``resolve_owner_type``) monkeypatch
``github._run``, the module's single subprocess chokepoint, with a fake that
returns canned ``CompletedProcess``-shaped results.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from issue_check import github


@pytest.mark.parametrize(
    "remote",
    [
        "git@github.com:an-org/a-repo.git",
        "git@github.com:an-org/a-repo",
        "https://github.com/an-org/a-repo.git",
        "https://github.com/an-org/a-repo",
        "https://github.com/an-org/a-repo/",
        "ssh://git@github.com/an-org/a-repo.git",
        "  git@github.com:an-org/a-repo.git\n",
    ],
)
def test_repo_from_remote_parses_github_urls(remote: str) -> None:
    assert github.repo_from_remote(remote) == "an-org/a-repo"


def test_run_missing_binary_raises_issue_check_error() -> None:
    with pytest.raises(github.IssueCheckError, match="no-such-binary-xyz"):
        github._run(["no-such-binary-xyz", "--version"])


def test_sprint_filter_defaults_to_iteration() -> None:
    assert github.sprint_filter("Iteration") == "iteration:@current"
    assert github.sprint_filter("Sprint") == "sprint:@current"
    assert github.sprint_filter("") == "iteration:@current"


def test_sprint_query_uses_org_root_and_interpolates_fields() -> None:
    query = github._sprint_query("an-org", "organization", 2, "Iteration", "Status")
    assert 'organization(login: "an-org")' in query
    assert "projectV2(number: 2)" in query
    assert 'fieldValueByName(name: "Status")' in query
    assert 'fieldValueByName(name: "Iteration")' in query
    assert 'query: $q' in query


def test_sprint_query_uses_user_root() -> None:
    query = github._sprint_query("a-user", "user", 5, "Sprint", "Column")
    assert 'user(login: "a-user")' in query
    assert 'fieldValueByName(name: "Column")' in query
    assert 'fieldValueByName(name: "Sprint")' in query


# --- board-field validation --------------------------------------------------


def _fields() -> list[dict[str, str]]:
    """A representative board: one iteration field, one single-select, one text."""
    return [
        {"name": "Sprint", "dataType": "ITERATION"},
        {"name": "Status", "dataType": "SINGLE_SELECT"},
        {"name": "Estimate", "dataType": "NUMBER"},
        {"name": "Notes", "dataType": "TEXT"},
    ]


def _fields_with_status_options() -> list[dict[str, Any]]:
    """A board whose Status field carries real (emoji-prefixed) options."""
    return [
        {"name": "Sprint", "dataType": "ITERATION"},
        {
            "name": "Status",
            "dataType": "SINGLE_SELECT",
            "options": [
                {"name": "🚀 Priority"},
                {"name": "In progress"},
                {"name": "Backlog"},
            ],
        },
    ]


def test_fields_query_uses_org_root_and_common_fragment() -> None:
    query = github._fields_query("an-org", "organization", 3)
    assert 'organization(login: "an-org")' in query
    assert "projectV2(number: 3)" in query
    assert "... on ProjectV2FieldCommon { name dataType }" in query


def test_fields_query_includes_single_select_options() -> None:
    query = github._fields_query("an-org", "organization", 3)
    assert "... on ProjectV2SingleSelectField { options { name } }" in query


def test_fields_query_uses_user_root() -> None:
    query = github._fields_query("a-user", "user", 7)
    assert 'user(login: "a-user")' in query


def test_validate_board_fields_accepts_matching_config() -> None:
    # Case-insensitive: configured names need not match the board's casing.
    github.validate_board_fields(_fields(), "sprint", "STATUS")


def test_validate_board_fields_rejects_missing_sprint_field() -> None:
    with pytest.raises(github.IssueCheckError) as excinfo:
        github.validate_board_fields(_fields(), "Cycle", "Status")
    message = str(excinfo.value)
    assert "sprintField" in message and '"Cycle"' in message
    assert "not a field" in message
    assert '"Sprint"' in message  # lists the board's real iteration field


def test_validate_board_fields_rejects_wrong_type_sprint_field() -> None:
    with pytest.raises(github.IssueCheckError) as excinfo:
        github.validate_board_fields(_fields(), "Status", "Status")
    message = str(excinfo.value)
    assert "sprintField" in message and '"Status"' in message
    assert "SINGLE_SELECT" in message  # names the actual (wrong) data type
    assert '"Sprint"' in message


def test_validate_board_fields_rejects_missing_status_field() -> None:
    with pytest.raises(github.IssueCheckError) as excinfo:
        github.validate_board_fields(_fields(), "Sprint", "Column")
    message = str(excinfo.value)
    assert "statusField" in message and '"Column"' in message
    assert '"Status"' in message  # lists the board's real single-select field


def test_validate_board_fields_rejects_wrong_type_status_field() -> None:
    with pytest.raises(github.IssueCheckError) as excinfo:
        github.validate_board_fields(_fields(), "Sprint", "Estimate")
    message = str(excinfo.value)
    assert "statusField" in message and "NUMBER" in message


def test_validate_board_fields_rejects_multiword_sprint_field() -> None:
    # #4: a multi-word iteration field can't be a filter qualifier — reject it
    # (path taken: reject, since the board filter only quotes values, not names).
    fields = [
        {"name": "Sprint Cycle", "dataType": "ITERATION"},
        {"name": "Status", "dataType": "SINGLE_SELECT"},
    ]
    with pytest.raises(github.IssueCheckError) as excinfo:
        github.validate_board_fields(fields, "Sprint Cycle", "Status")
    message = str(excinfo.value)
    assert "single-word" in message
    assert "sprint cycle:@current" in message  # shows the broken token


# --- statusOrder validation (#7) ----------------------------------------------


def test_validate_board_fields_accepts_normalized_status_order() -> None:
    # Configured as displayed ("Priority", any case) against a board option
    # that carries an emoji ("🚀 Priority") — the same normalisation as
    # model.status_rank, so this must not raise.
    github.validate_board_fields(
        _fields_with_status_options(),
        "Sprint",
        "Status",
        ("Priority", "BACKLOG", "in progress"),
    )


def test_validate_board_fields_ignores_status_order_when_not_given() -> None:
    # No status_order passed -> defaults to () -> validation is skipped entirely,
    # matching every other existing call site of validate_board_fields.
    github.validate_board_fields(_fields_with_status_options(), "Sprint", "Status")


def test_validate_board_fields_rejects_unknown_status_order_entry() -> None:
    with pytest.raises(github.IssueCheckError) as excinfo:
        github.validate_board_fields(
            _fields_with_status_options(),
            "Sprint",
            "Status",
            ("Priority", "Blocked"),
        )
    message = str(excinfo.value)
    assert "statusOrder" in message and '"Blocked"' in message
    # the board's real options are listed, emoji-stripped (what the user sees)
    assert '"Priority"' in message
    assert '"In progress"' in message
    assert '"Backlog"' in message
    assert "🚀" not in message


# --- owner_search_query ------------------------------------------------------


@pytest.mark.parametrize(
    "owner_type,qualifier",
    [("organization", "org"), ("user", "user")],
)
def test_owner_search_query_uses_owner_type_qualifier(
    owner_type: str, qualifier: str
) -> None:
    query = github.owner_search_query("acme", owner_type, "alice", mine=False)
    assert query.startswith(f"{qualifier}:acme ")
    assert "assignee:" not in query


@pytest.mark.parametrize("owner_type", ["organization", "user"])
def test_owner_search_query_always_excludes_archived_and_sorts(
    owner_type: str,
) -> None:
    query = github.owner_search_query("acme", owner_type, "alice", mine=False)
    assert "archived:false" in query
    assert "sort:updated-desc" in query
    assert "is:issue" in query
    assert "is:open" in query


def test_owner_search_query_mine_appends_assignee_filter() -> None:
    query = github.owner_search_query("acme", "organization", "alice", mine=True)
    assert query.endswith("assignee:alice")


def test_owner_search_query_mine_false_omits_assignee_filter() -> None:
    query = github.owner_search_query("acme", "organization", "alice", mine=False)
    assert "assignee" not in query


# --- resolve_owner_type -------------------------------------------------------


def _fake_run_with_stdout(
    stdout: str, returncode: int = 0
) -> Any:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, returncode, stdout=stdout, stderr="" if returncode == 0 else stdout
        )

    return fake_run


def test_resolve_owner_type_maps_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="Organization\n", stderr="")

    monkeypatch.setattr(github, "_run", fake_run)
    assert github.resolve_owner_type("acme") == "organization"
    assert captured["args"] == ["gh", "api", "users/acme", "--jq", ".type"]


def test_resolve_owner_type_maps_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "_run", _fake_run_with_stdout("User\n"))
    assert github.resolve_owner_type("octocat") == "user"


def test_resolve_owner_type_gh_failure_raises_with_owner_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="HTTP 404: Not Found"
        )

    monkeypatch.setattr(github, "_run", fake_run)
    with pytest.raises(github.IssueCheckError, match="no-such-owner"):
        github.resolve_owner_type("no-such-owner")


def test_resolve_owner_type_unexpected_type_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(github, "_run", _fake_run_with_stdout("Bot\n"))
    with pytest.raises(github.IssueCheckError, match="acme-bot"):
        github.resolve_owner_type("acme-bot")


# --- _search_issues / fetch_assigned_issues / fetch_owner_issues -------------


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


def _paged_fake_run(pages: list[str]) -> Any:
    """A fake ``_run`` that returns ``pages`` in order, one gh call each."""
    calls = {"n": 0}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        stdout = pages[calls["n"]]
        calls["n"] += 1
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    fake_run.calls = calls  # type: ignore[attr-defined]
    return fake_run


def test_fetch_assigned_issues_single_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github,
        "_run",
        _paged_fake_run([_search_payload(2, [{"number": 1}, {"number": 2}])]),
    )
    issues, total = github.fetch_assigned_issues("an-org/a-repo", "alice", 10)
    assert [i["number"] for i in issues] == [1, 2]
    assert total == 2


def test_fetch_assigned_issues_gh_failure_raises_with_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

    monkeypatch.setattr(github, "_run", fake_run)
    with pytest.raises(github.IssueCheckError, match="an-org/a-repo"):
        github.fetch_assigned_issues("an-org/a-repo", "alice", 10)


def test_fetch_owner_issues_issues_the_expected_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(
            args, 0, stdout=_search_payload(0, []), stderr=""
        )

    monkeypatch.setattr(github, "_run", fake_run)
    github.fetch_owner_issues("acme", "organization", "alice", 10, mine=True)

    q_arg = next(a for a in captured["args"] if a.startswith("q="))
    expected = github.owner_search_query("acme", "organization", "alice", mine=True)
    assert q_arg == f"q={expected}"


def test_fetch_owner_issues_paginates_across_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        _search_payload(
            3, [{"number": 1}, {"number": 2}], has_next=True, end_cursor="CURSOR1"
        ),
        _search_payload(3, [{"number": 3}]),
    ]
    fake_run = _paged_fake_run(pages)
    monkeypatch.setattr(github, "_run", fake_run)

    issues, total = github.fetch_owner_issues(
        "acme", "organization", "alice", 10, mine=False
    )
    assert [i["number"] for i in issues] == [1, 2, 3]
    assert total == 3
    assert fake_run.calls["n"] == 2  # type: ignore[attr-defined]


def test_fetch_owner_issues_truncates_at_limit(
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

    monkeypatch.setattr(github, "_run", fake_run)
    issues, total = github.fetch_owner_issues(
        "acme", "organization", "alice", 5, mine=False
    )
    assert len(issues) == 5
    assert total == 100  # server total can exceed what limit lets through


def test_fetch_owner_issues_gh_failure_raises_with_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

    monkeypatch.setattr(github, "_run", fake_run)
    with pytest.raises(github.IssueCheckError, match="acme"):
        github.fetch_owner_issues("acme", "organization", "alice", 5, mine=False)
