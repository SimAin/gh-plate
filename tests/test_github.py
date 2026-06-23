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
