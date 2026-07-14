"""Tests for issue_check.config — special-label configuration."""

from __future__ import annotations

import json

import pytest

from issue_check import config
from issue_check.github import IssueCheckError


def test_defaults_when_no_file(tmp_path) -> None:
    missing = tmp_path / "nope.json"
    cfg = config.load_config(str(missing))
    assert cfg.style_for("blocked") == "alert"   # built-in default
    assert cfg.style_for("whatever") is None


def test_user_file_merges_over_defaults(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"labels": {"needs-info": "warn", "blocked": "hide"}}))
    cfg = config.load_config(str(path))
    assert cfg.style_for("needs-info") == "warn"
    assert cfg.style_for("blocked") == "hide"     # user overrides the default


def test_style_for_is_case_insensitive(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"labels": {"Blocked": "alert"}}))
    cfg = config.load_config(str(path))
    assert cfg.style_for("BLOCKED") == "alert"
    assert cfg.style_for("blocked") == "alert"


def test_style_for_supports_globs(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"labels": {"status:*": "warn"}}))
    cfg = config.load_config(str(path))
    assert cfg.style_for("status:in-review") == "warn"
    assert cfg.style_for("priority:high") is None


def test_unknown_style_is_rejected(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"labels": {"blocked": "explode"}}))
    with pytest.raises(IssueCheckError, match="Unknown style"):
        config.load_config(str(path))


def test_malformed_json_is_rejected(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json")
    with pytest.raises(IssueCheckError, match="Could not read config"):
        config.load_config(str(path))


def test_config_path_honours_env(monkeypatch) -> None:
    monkeypatch.setenv("ISSUE_CHECK_CONFIG", "/custom/loc.json")
    assert config.config_path() == "/custom/loc.json"


# --- sprint board (repos block) ----------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://github.com/orgs/an-org/projects/2", ("an-org", "organization", 2)),
        ("https://github.com/users/a-user/projects/5", ("a-user", "user", 5)),
        (
            "https://github.com/orgs/an-org/projects/2/views/2",
            ("an-org", "organization", 2),
        ),
        ("an-org/projects/7", ("an-org", "organization", 7)),
        (
            "  https://github.com/orgs/an-org/projects/2  ",
            ("an-org", "organization", 2),
        ),
    ],
)
def test_parse_project_url(value, expected) -> None:
    assert config.parse_project_url(value) == expected


def test_parse_project_url_rejects_garbage() -> None:
    with pytest.raises(IssueCheckError, match="Could not parse project reference"):
        config.parse_project_url("https://example.com/not-a-project")


def test_repos_block_parses_with_defaults(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "repos": {
                    "an-org/a-repo": {
                        "project": "https://github.com/orgs/an-org/projects/2"
                    }
                }
            }
        )
    )
    cfg = config.load_config(str(path))
    project = cfg.project_for("an-org/a-repo")
    assert project is not None
    assert (project.owner, project.owner_type, project.number) == (
        "an-org",
        "organization",
        2,
    )
    assert project.sprint_field == "Iteration"   # GitHub defaults
    assert project.status_field == "Status"
    assert project.status_order == ()
    assert cfg.project_for("an-org/other-repo") is None


def test_repos_block_honours_field_and_status_overrides(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "repos": {
                    "an-org/a-repo": {
                        "project": "an-org/projects/2",
                        "sprintField": "Sprint",
                        "statusField": "Column",
                        "statusOrder": ["In progress", "Todo"],
                    }
                }
            }
        )
    )
    project = config.load_config(str(path)).project_for("an-org/a-repo")
    assert project is not None
    assert project.sprint_field == "Sprint"
    assert project.status_field == "Column"
    assert project.status_order == ("In progress", "Todo")


def test_repos_requires_project(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"repos": {"an-org/a-repo": {}}}))
    with pytest.raises(IssueCheckError, match='needs a "project"'):
        config.load_config(str(path))


def test_repos_status_order_must_be_list_of_strings(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "repos": {
                    "an-org/a-repo": {
                        "project": "an-org/projects/2",
                        "statusOrder": "In progress",
                    }
                }
            }
        )
    )
    with pytest.raises(IssueCheckError, match='"statusOrder" must be a list'):
        config.load_config(str(path))
