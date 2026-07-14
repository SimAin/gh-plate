"""Tests for the pure URL parsing in issue_check.github.

Only the regex paths are exercised — they match before the ``gh`` subprocess
fallback, so these tests never shell out.
"""

from __future__ import annotations

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
