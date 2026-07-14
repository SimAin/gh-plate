"""Tests for the pure URL parsing in issue_check.github.

Only the regex paths are exercised — they match before the ``gh`` subprocess
fallback, so these tests never shell out.
"""

from __future__ import annotations

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


def test_fields_query_uses_org_root_and_common_fragment() -> None:
    query = github._fields_query("an-org", "organization", 3)
    assert 'organization(login: "an-org")' in query
    assert "projectV2(number: 3)" in query
    assert "... on ProjectV2FieldCommon { name dataType }" in query


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
