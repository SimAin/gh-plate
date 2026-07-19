"""Tests for plate.cli (top-level dispatch) and plate.issues.cli (the
``issues`` subcommand: flags + run() dispatch)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from plate import __version__, cli
from plate.core import config, gh
from plate.core.gh import PlateError
from plate.issues import cli as issues_cli
from plate.issues import github


def test_version_flag_prints_version_and_exits(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(["--version"])
    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_bare_plate_prints_help_and_hint(capsys) -> None:
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "plate issues" in out
    assert "plate prs" in out
    assert "Hint:" in out


def test_limit_zero_is_rejected(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["issues", "--limit", "0"])
    assert excinfo.value.code == 2
    assert "positive integer" in capsys.readouterr().err


def test_limit_negative_is_rejected(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["issues", "--limit", "-5"])
    assert excinfo.value.code == 2
    assert "positive integer" in capsys.readouterr().err


def test_stale_days_zero_is_rejected(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["issues", "--stale-days", "0"])
    assert excinfo.value.code == 2
    assert "positive integer" in capsys.readouterr().err


def test_valid_limit_still_parses() -> None:
    args = cli.parse_args(["issues", "--limit", "10"])
    assert args.limit == 10


def test_valid_stale_days_still_parses() -> None:
    args = cli.parse_args(["issues", "--stale-days", "30"])
    assert args.stale_days == 30


def test_defaults_when_not_given() -> None:
    args = cli.parse_args(["issues"])
    assert args.limit == issues_cli.DEFAULT_LIMIT
    assert args.stale_days == issues_cli.DEFAULT_STALE_DAYS


# --- owner view (--owner) ----------------------------------------------------


def _issue(
    number: int,
    *,
    repo: str = "an-org/repo-a",
    assignees: tuple[str, ...] = ("me",),
    updated_days_ago: int = 0,
) -> dict[str, Any]:
    """A minimal owner-search issue payload (carries its own repository)."""
    updated = (datetime.now(UTC) - timedelta(days=updated_days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://github.com/{repo}/issues/{number}",
        "updatedAt": updated,
        "labels": {"nodes": []},
        "comments": {"totalCount": 0},
        "subIssuesSummary": {"total": 0, "completed": 0},
        "closedByPullRequestsReferences": {"nodes": []},
        "repository": {"nameWithOwner": repo},
        "assignees": {"nodes": [{"login": a} for a in assignees]},
    }


def _boom_current_repo() -> str:
    raise AssertionError("current_repo() must not be called on the --owner path")


def _stub_owner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    issues: list[dict[str, Any]],
    total: int,
    cfg: config.Config | None = None,
    owner_type: str = "organization",
    login: str = "me",
) -> dict[str, Any]:
    """Wire the owner path's I/O to in-memory stubs; record fetch arguments."""
    calls: dict[str, Any] = {}
    monkeypatch.setattr(
        config, "load_config", lambda *a, **k: cfg or config.Config()
    )
    monkeypatch.setattr(gh, "current_login", lambda: login)
    monkeypatch.setattr(gh, "current_repo", _boom_current_repo)
    monkeypatch.setattr(gh, "resolve_owner_type", lambda owner: owner_type)

    def fake_fetch(
        owner: str, otype: str, lg: str, limit: int, *, mine: bool
    ) -> tuple[list[dict[str, Any]], int]:
        calls.update(owner=owner, owner_type=otype, login=lg, limit=limit, mine=mine)
        return issues, total

    monkeypatch.setattr(github, "fetch_owner_issues", fake_fetch)
    return calls


def test_owner_and_repo_are_mutually_exclusive(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["issues", "--owner", "an-org", "--repo", "an-org/a-repo"])
    assert excinfo.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_mine_without_owner_errors() -> None:
    with pytest.raises(PlateError) as excinfo:
        issues_cli.run(cli.parse_args(["issues", "--mine"]))
    assert "--mine only applies with --owner" in str(excinfo.value)


def test_sprint_with_owner_errors() -> None:
    with pytest.raises(PlateError) as excinfo:
        issues_cli.run(cli.parse_args(["issues", "--owner", "an-org", "--sprint"]))
    assert "--sprint is per-repo" in str(excinfo.value)


def test_owner_flow_never_calls_current_repo(monkeypatch, capsys) -> None:
    _stub_owner(monkeypatch, issues=[_issue(1)], total=1)
    # _boom_current_repo would raise if the owner path touched it.
    assert issues_cli.run(cli.parse_args(["issues", "--owner", "an-org"])) == 0
    assert "repo-a" in capsys.readouterr().out


def test_owner_alias_resolves_and_shows_arrow(monkeypatch, capsys) -> None:
    cfg = config.Config(owners={"work": "company-org"})
    calls = _stub_owner(monkeypatch, issues=[_issue(1)], total=1, cfg=cfg)
    assert issues_cli.run(cli.parse_args(["issues", "--owner", "work"])) == 0
    assert calls["owner"] == "company-org"  # fetch got the resolved name
    assert "work → company-org" in capsys.readouterr().out


def test_owner_literal_shows_no_arrow(monkeypatch, capsys) -> None:
    calls = _stub_owner(monkeypatch, issues=[_issue(1)], total=1)
    assert issues_cli.run(cli.parse_args(["issues", "--owner", "an-org"])) == 0
    assert calls["owner"] == "an-org"
    assert "→" not in capsys.readouterr().out


def test_owner_type_failure_lists_aliases(monkeypatch, capsys) -> None:
    cfg = config.Config(owners={"work": "company-org", "personal": "my-org"})
    _stub_owner(monkeypatch, issues=[], total=0, cfg=cfg)

    def fail(owner: str) -> str:
        raise PlateError(f"GitHub owner '{owner}' not found or not accessible.")

    monkeypatch.setattr(gh, "resolve_owner_type", fail)
    with pytest.raises(PlateError) as excinfo:
        issues_cli.run(cli.parse_args(["issues", "--owner", "typo"]))
    message = str(excinfo.value)
    assert "Configured aliases:" in message
    assert "work → company-org" in message
    assert "personal → my-org" in message


def test_owner_type_failure_without_aliases_has_no_alias_line(monkeypatch) -> None:
    _stub_owner(monkeypatch, issues=[], total=0)

    def fail(owner: str) -> str:
        raise PlateError(f"GitHub owner '{owner}' not found or not accessible.")

    monkeypatch.setattr(gh, "resolve_owner_type", fail)
    with pytest.raises(PlateError) as excinfo:
        issues_cli.run(cli.parse_args(["issues", "--owner", "nope"]))
    assert "Configured aliases:" not in str(excinfo.value)


def test_owner_empty_default_message(monkeypatch, capsys) -> None:
    _stub_owner(monkeypatch, issues=[], total=0)
    assert issues_cli.run(cli.parse_args(["issues", "--owner", "an-org"])) == 0
    assert "No open issues found for an-org." in capsys.readouterr().out


def test_owner_empty_mine_message(monkeypatch, capsys) -> None:
    calls = _stub_owner(monkeypatch, issues=[], total=0)
    assert issues_cli.run(
        cli.parse_args(["issues", "--owner", "an-org", "--mine"])
    ) == 0
    assert calls["mine"] is True
    assert "No open issues assigned to you for an-org." in capsys.readouterr().out


def test_owner_truncation_note_limit_hit(monkeypatch, capsys) -> None:
    issues = [_issue(n) for n in range(1, 3)]
    _stub_owner(monkeypatch, issues=issues, total=5)
    assert issues_cli.run(
        cli.parse_args(["issues", "--owner", "an-org", "--limit", "2"])
    ) == 0
    err = capsys.readouterr().err
    assert "showing 2 of 5 open issues for an-org (--limit 2)." in err


def test_owner_truncation_note_search_ceiling(monkeypatch, capsys) -> None:
    issues = [_issue(n) for n in range(1, 4)]
    _stub_owner(monkeypatch, issues=issues, total=1500)
    assert issues_cli.run(cli.parse_args(["issues", "--owner", "an-org"])) == 0
    err = capsys.readouterr().err
    assert "at most 1000 results per query" in err
    assert "showing 3 of 1500 open issues for an-org" in err


def test_owner_no_note_when_complete(monkeypatch, capsys) -> None:
    issues = [_issue(1), _issue(2)]
    _stub_owner(monkeypatch, issues=issues, total=2)
    assert issues_cli.run(cli.parse_args(["issues", "--owner", "an-org"])) == 0
    assert "Note:" not in capsys.readouterr().err


def test_owner_markdown_format(monkeypatch, capsys) -> None:
    _stub_owner(monkeypatch, issues=[_issue(1)], total=1)
    assert issues_cli.run(
        cli.parse_args(["issues", "--owner", "an-org", "--format", "markdown"])
    ) == 0
    assert "## an-org/repo-a" in capsys.readouterr().out


def test_owner_show_key_prints_owner_key(monkeypatch, capsys) -> None:
    _stub_owner(monkeypatch, issues=[_issue(1)], total=1)
    assert issues_cli.run(
        cli.parse_args(["issues", "--owner", "an-org", "--show-key"])
    ) == 0
    out = capsys.readouterr().out
    assert "Key" in out
    assert "most recently active repo" in out
