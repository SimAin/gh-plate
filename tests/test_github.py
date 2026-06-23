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
