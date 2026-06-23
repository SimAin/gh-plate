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
