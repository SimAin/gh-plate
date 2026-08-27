"""Tests for plate.core.config — special-label configuration."""

from __future__ import annotations

import json

import pytest

from plate.core import config
from plate.core.gh import PlateError


def test_defaults_when_no_file(tmp_path) -> None:
    missing = tmp_path / "nope.json"
    cfg = config.load_config(str(missing))
    assert cfg.style_for("blocked") == "alert"  # built-in default
    assert cfg.style_for("whatever") is None


def test_user_file_merges_over_defaults(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"labels": {"needs-info": "warn", "blocked": "hide"}}))
    cfg = config.load_config(str(path))
    assert cfg.style_for("needs-info") == "warn"
    assert cfg.style_for("blocked") == "hide"  # user overrides the default


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
    with pytest.raises(PlateError, match="Unknown style"):
        config.load_config(str(path))


def test_unknown_top_level_key_warns_with_suggestion(tmp_path, capsys) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"lables": {"blocked": "alert"}}))
    config.load_config(str(path))
    err = capsys.readouterr().err
    assert 'unrecognised config key "lables"' in err
    assert 'did you mean "labels"?' in err


def test_unknown_repo_level_key_warns_naming_the_repo(tmp_path, capsys) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "repos": {
                    "an-org/a-repo": {
                        "project": "an-org/projects/2",
                        "sprintfield": "Sprint",
                    }
                }
            }
        )
    )
    config.load_config(str(path))
    err = capsys.readouterr().err
    assert 'unrecognised config key "sprintfield"' in err
    assert "an-org/a-repo" in err
    assert 'did you mean "sprintField"?' in err


def test_valid_config_produces_no_stderr(tmp_path, capsys) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "labels": {"blocked": "alert"},
                "repos": {"an-org/a-repo": {"project": "an-org/projects/2"}},
                "owners": {"work": "my-work-org"},
            }
        )
    )
    config.load_config(str(path))
    assert capsys.readouterr().err == ""


def test_unknown_keys_never_raise(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"totally-unknown": 42, "labels": {"blocked": "alert"}}))
    cfg = config.load_config(str(path))
    assert cfg.style_for("blocked") == "alert"


def test_malformed_json_is_rejected(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json")
    with pytest.raises(PlateError, match="Could not read config"):
        config.load_config(str(path))


# --- config_path() resolution --------------------------------------------------


def test_config_path_plate_config_env_wins(tmp_path, monkeypatch) -> None:
    override = tmp_path / "elsewhere.json"
    monkeypatch.setenv("PLATE_CONFIG", str(override))
    assert config.config_path() == str(override)


def test_config_path_default_under_xdg_config_home(tmp_path) -> None:
    expected = tmp_path / ".config" / "plate" / "config.json"
    assert config.config_path() == str(expected)


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
    with pytest.raises(PlateError, match="Could not parse project reference"):
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
    assert project.sprint_field == "Iteration"  # GitHub defaults
    assert project.status_field == "Status"
    assert project.status_order == ()
    assert cfg.project_for("an-org/other-repo") is None


def test_project_for_is_case_insensitive(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "repos": {
                    "An-Org/A-Repo": {
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


def test_project_for_matches_config_key_regardless_of_lookup_casing(
    tmp_path,
) -> None:
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
    project = cfg.project_for("AN-ORG/A-REPO")
    assert project is not None
    assert project.owner == "an-org"
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
    with pytest.raises(PlateError, match='needs a "project"'):
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
    with pytest.raises(PlateError, match='"statusOrder" must be a list'):
        config.load_config(str(path))


# --- owner aliases (owners block) ---------------------------------------------


def test_owners_block_parses(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"owners": {"work": "my-work-org"}}))
    cfg = config.load_config(str(path))
    assert cfg.owners == {"work": "my-work-org"}


def test_owners_keys_are_normalized(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"owners": {"  Work  ": "my-work-org"}}))
    cfg = config.load_config(str(path))
    assert cfg.owners == {"work": "my-work-org"}
    assert cfg.resolve_owner("Work") == "my-work-org"
    assert cfg.resolve_owner("  WORK  ") == "my-work-org"


def test_owners_block_must_be_an_object(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"owners": ["work", "my-work-org"]}))
    with pytest.raises(PlateError, match='"owners" must be an object'):
        config.load_config(str(path))


def test_owners_value_must_be_a_string(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"owners": {"work": 123}}))
    with pytest.raises(PlateError, match='"owners" must be an object'):
        config.load_config(str(path))


def test_owners_value_must_not_be_empty(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"owners": {"work": "   "}}))
    with pytest.raises(PlateError, match='"owners" must be an object'):
        config.load_config(str(path))


def test_owners_alias_must_not_be_empty(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"owners": {"   ": "my-work-org"}}))
    with pytest.raises(PlateError, match='"owners" must be an object'):
        config.load_config(str(path))


def test_resolve_owner_alias_hit(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"owners": {"personal": "my-projects-org"}}))
    cfg = config.load_config(str(path))
    assert cfg.resolve_owner("personal") == "my-projects-org"


def test_resolve_owner_is_case_insensitive(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"owners": {"work": "my-work-org"}}))
    cfg = config.load_config(str(path))
    assert cfg.resolve_owner("WORK") == "my-work-org"


def test_resolve_owner_falls_through_to_literal_for_unknown_names(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"owners": {"work": "my-work-org"}}))
    cfg = config.load_config(str(path))
    assert cfg.resolve_owner("some-other-org") == "some-other-org"


def test_resolve_owner_alias_shadows_literal_of_same_name(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"owners": {"acme": "other-org"}}))
    cfg = config.load_config(str(path))
    assert cfg.resolve_owner("acme") == "other-org"


def test_resolve_owner_without_owners_block_is_always_literal(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"labels": {"blocked": "alert"}}))
    cfg = config.load_config(str(path))
    assert cfg.resolve_owner("anything") == "anything"
    assert cfg.resolve_owner("Some-Org") == "Some-Org"


def test_resolve_owner_default_config_is_always_literal(tmp_path) -> None:
    missing = tmp_path / "nope.json"
    cfg = config.load_config(str(missing))
    assert cfg.owners == {}
    assert cfg.resolve_owner("anything") == "anything"
