"""Tests for plate.cli (top-level dispatch) and plate.issues.cli (the
``issues`` subcommand: flags + run() dispatch)."""

from __future__ import annotations

import io
import os
import sys
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


def test_bare_plate_prints_help_and_hint(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "terminal_width", lambda: 80)
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "plate issues" in out
    assert "plate prs" in out
    assert "plate retro" in out
    hint_lines = out[out.index("Hint:") :].splitlines()
    assert len(hint_lines) >= 2  # wrapped to the (patched) terminal width
    assert all(len(line) <= 78 for line in hint_lines)


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


def test_config_path_flag_prints_explicit_config_and_exits_zero(capsys) -> None:
    # Returns before any config load or gh call — nothing is stubbed here.
    args = cli.parse_args(["issues", "--config-path", "--config", "/tmp/plate.json"])
    assert issues_cli.run(args) == 0
    assert capsys.readouterr().out.strip() == "/tmp/plate.json"


def test_config_path_flag_defaults_to_resolved_location(monkeypatch, capsys) -> None:
    monkeypatch.setattr(config, "config_path", lambda: "/somewhere/config.json")
    assert issues_cli.run(cli.parse_args(["issues", "--config-path"])) == 0
    assert capsys.readouterr().out.strip() == "/somewhere/config.json"


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


def _boom_current_login() -> str | None:
    raise AssertionError(
        "current_login() must not be called — the viewer login arrives in "
        "each path's own GraphQL query"
    )


def _stub_owner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    issues: list[dict[str, Any]],
    total: int,
    cfg: config.Config | None = None,
    owner_type: str = "organization",
    viewer: str | None = "me",
) -> dict[str, Any]:
    """Wire the owner path's I/O to in-memory stubs; record fetch arguments."""
    calls: dict[str, Any] = {}
    monkeypatch.setattr(config, "load_config", lambda *a, **k: cfg or config.Config())
    monkeypatch.setattr(gh, "current_login", _boom_current_login)
    monkeypatch.setattr(gh, "current_repo", _boom_current_repo)
    monkeypatch.setattr(gh, "resolve_owner_type", lambda owner: owner_type)

    def fake_fetch(
        owner: str, otype: str, limit: int, *, assignee: str | None
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        calls.update(owner=owner, owner_type=otype, limit=limit, assignee=assignee)
        return issues, total, viewer

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
    assert (
        issues_cli.run(cli.parse_args(["issues", "--owner", "an-org", "--mine"])) == 0
    )
    assert calls["assignee"] == "@me"
    assert "No open issues assigned to you for an-org." in capsys.readouterr().out


def test_owner_default_searches_without_assignee(monkeypatch) -> None:
    calls = _stub_owner(monkeypatch, issues=[_issue(1)], total=1)
    assert issues_cli.run(cli.parse_args(["issues", "--owner", "an-org"])) == 0
    assert calls["assignee"] is None


def test_owner_viewer_missing_raises_login_error(monkeypatch) -> None:
    # The login for yours/others grouping rides on the fetch itself; when the
    # response somehow lacks it, the old actionable auth error must survive.
    _stub_owner(monkeypatch, issues=[_issue(1)], total=1, viewer=None)
    with pytest.raises(PlateError) as excinfo:
        issues_cli.run(cli.parse_args(["issues", "--owner", "an-org"]))
    assert "Could not determine your GitHub login" in str(excinfo.value)


def test_owner_viewer_missing_with_empty_result_still_reports(
    monkeypatch, capsys
) -> None:
    # An empty result needs no grouping, so no login is required to say so.
    _stub_owner(monkeypatch, issues=[], total=0, viewer=None)
    assert issues_cli.run(cli.parse_args(["issues", "--owner", "an-org"])) == 0
    assert "No open issues found for an-org." in capsys.readouterr().out


def test_owner_truncation_note_limit_hit(monkeypatch, capsys) -> None:
    issues = [_issue(n) for n in range(1, 3)]
    _stub_owner(monkeypatch, issues=issues, total=5)
    assert (
        issues_cli.run(cli.parse_args(["issues", "--owner", "an-org", "--limit", "2"]))
        == 0
    )
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
    assert (
        issues_cli.run(
            cli.parse_args(["issues", "--owner", "an-org", "--format", "markdown"])
        )
        == 0
    )
    assert "## an-org/repo-a" in capsys.readouterr().out


def test_owner_markdown_alias_shows_display_line(monkeypatch, capsys) -> None:
    cfg = config.Config(owners={"work": "company-org"})
    _stub_owner(monkeypatch, issues=[_issue(1)], total=1, cfg=cfg)
    assert (
        issues_cli.run(
            cli.parse_args(["issues", "--owner", "work", "--format", "markdown"])
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "*work → company-org*" in out
    assert "## an-org/repo-a" in out


def test_owner_show_key_prints_owner_key(monkeypatch, capsys) -> None:
    _stub_owner(monkeypatch, issues=[_issue(1)], total=1)
    assert (
        issues_cli.run(cli.parse_args(["issues", "--owner", "an-org", "--show-key"]))
        == 0
    )
    out = capsys.readouterr().out
    assert "Key" in out
    assert "most recently active repo" in out


# --- the default (yours) view: viewer login rides on the fetch ----------------


def _stub_yours(
    monkeypatch: pytest.MonkeyPatch,
    *,
    issues: list[dict[str, Any]],
    total: int,
    viewer: str | None = "me",
) -> dict[str, Any]:
    """Wire the default path's I/O to in-memory stubs; record fetch arguments."""
    calls: dict[str, Any] = {}
    monkeypatch.setattr(config, "load_config", lambda *a, **k: config.Config())
    monkeypatch.setattr(gh, "current_login", _boom_current_login)
    monkeypatch.setattr(gh, "current_repo", lambda: "an-org/a-repo")

    def fake_fetch(
        repo: str, limit: int, *, assignee: str = "@me"
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        calls.update(repo=repo, limit=limit, assignee=assignee)
        return issues, total, viewer

    monkeypatch.setattr(github, "fetch_assigned_issues", fake_fetch)
    return calls


def test_yours_flow_never_calls_current_login(monkeypatch, capsys) -> None:
    # _boom_current_login would raise if the hot path still did the gh api
    # user round trip; assignee:@me makes the concrete login unnecessary.
    calls = _stub_yours(monkeypatch, issues=[_issue(1)], total=1)
    assert issues_cli.run(cli.parse_args(["issues"])) == 0
    assert calls["repo"] == "an-org/a-repo"
    assert "Issue 1" in capsys.readouterr().out


def test_yours_renders_even_without_viewer(monkeypatch, capsys) -> None:
    # The yours view groups nothing by login, so a missing viewer is inert.
    _stub_yours(monkeypatch, issues=[_issue(1)], total=1, viewer=None)
    assert issues_cli.run(cli.parse_args(["issues"])) == 0
    assert "Issue 1" in capsys.readouterr().out


def test_yours_empty_prints_message(monkeypatch, capsys) -> None:
    _stub_yours(monkeypatch, issues=[], total=0)
    assert issues_cli.run(cli.parse_args(["issues"])) == 0
    out = capsys.readouterr().out
    assert "No open issues assigned to you in an-org/a-repo." in out


def test_yours_markdown_format(monkeypatch, capsys) -> None:
    _stub_yours(monkeypatch, issues=[_issue(1)], total=1)
    assert issues_cli.run(cli.parse_args(["issues", "--format", "markdown"])) == 0
    out = capsys.readouterr().out
    assert "[#1](https://github.com/an-org/repo-a/issues/1)" in out
    assert "Issue 1" in out


def test_yours_show_key_prints_key(monkeypatch, capsys) -> None:
    _stub_yours(monkeypatch, issues=[_issue(1)], total=1)
    assert issues_cli.run(cli.parse_args(["issues", "--show-key"])) == 0
    out = capsys.readouterr().out
    assert "Key" in out
    assert "Issue 1" in out


def test_yours_truncation_note_goes_to_stderr(monkeypatch, capsys) -> None:
    _stub_yours(monkeypatch, issues=[_issue(1), _issue(2)], total=5)
    assert issues_cli.run(cli.parse_args(["issues", "--limit", "2"])) == 0
    out, err = capsys.readouterr()
    assert "Note: showing 2 of 5 assigned issues." in err
    assert "Note:" not in out  # stdout stays clean for piping


def test_yours_no_truncation_note_when_complete(monkeypatch, capsys) -> None:
    _stub_yours(monkeypatch, issues=[_issue(1), _issue(2)], total=2)
    assert issues_cli.run(cli.parse_args(["issues", "--limit", "2"])) == 0
    assert "Note:" not in capsys.readouterr().err


# --- sprint view: viewer login rides on the items fetch -----------------------


def _sprint_cfg() -> config.Config:
    project = config.ProjectConfig(owner="an-org", owner_type="organization", number=2)
    return config.Config(projects={"an-org/a-repo": project})


def _sprint_item(
    number: int,
    *,
    repo: str = "an-org/a-repo",
    assignees: tuple[str, ...] = ("me",),
    iteration: str = "Sprint 7",
) -> dict[str, Any]:
    """A minimal board item whose content is an Issue in ``repo``."""
    return {
        "content": {
            "__typename": "Issue",
            "number": number,
            "title": f"Item {number}",
            "url": f"https://github.com/{repo}/issues/{number}",
            "updatedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "repository": {"nameWithOwner": repo},
            "assignees": {"nodes": [{"login": a} for a in assignees]},
            "labels": {"nodes": []},
            "comments": {"totalCount": 0},
            "subIssuesSummary": {"total": 0, "completed": 0},
            "closedByPullRequestsReferences": {"nodes": []},
        },
        "status": {"name": "In progress"},
        "iteration": {"title": iteration},
    }


def _stub_sprint(
    monkeypatch: pytest.MonkeyPatch,
    *,
    viewer: str | None,
    items: list[dict[str, Any]] | None = None,
) -> None:
    monkeypatch.setattr(config, "load_config", lambda *a, **k: _sprint_cfg())
    monkeypatch.setattr(gh, "current_login", _boom_current_login)
    monkeypatch.setattr(gh, "current_repo", lambda: "an-org/a-repo")
    monkeypatch.setattr(
        github,
        "fetch_project_fields",
        lambda *a, **k: [
            {"name": "Iteration", "dataType": "ITERATION"},
            {"name": "Status", "dataType": "SINGLE_SELECT"},
        ],
    )
    monkeypatch.setattr(
        github, "fetch_sprint_items", lambda *a, **k: (items or [], viewer)
    )


def test_sprint_flow_never_calls_current_login(monkeypatch, capsys) -> None:
    _stub_sprint(monkeypatch, viewer="me")
    assert issues_cli.run(cli.parse_args(["issues", "--sprint"])) == 0
    assert "No active sprint" in capsys.readouterr().out


def test_sprint_viewer_missing_raises_login_error(monkeypatch) -> None:
    _stub_sprint(monkeypatch, viewer=None)
    with pytest.raises(PlateError) as excinfo:
        issues_cli.run(cli.parse_args(["issues", "--sprint"]))
    assert "Could not determine your GitHub login" in str(excinfo.value)


def test_sprint_without_configured_board_raises_naming_config(monkeypatch) -> None:
    monkeypatch.setattr(config, "load_config", lambda *a, **k: config.Config())
    monkeypatch.setattr(gh, "current_repo", lambda: "an-org/a-repo")
    with pytest.raises(PlateError) as excinfo:
        issues_cli.run(
            cli.parse_args(["issues", "--sprint", "--config", "/tmp/plate.json"])
        )
    message = str(excinfo.value)
    assert "No sprint board configured for an-org/a-repo" in message
    assert "/tmp/plate.json" in message


def test_sprint_empty_with_title_names_the_sprint(monkeypatch, capsys) -> None:
    # An active iteration whose only items belong to another repo: the sprint
    # exists but has nothing to show here — distinct from "no active sprint".
    _stub_sprint(
        monkeypatch,
        viewer="me",
        items=[_sprint_item(1, repo="an-org/other-repo")],
    )
    assert issues_cli.run(cli.parse_args(["issues", "--sprint"])) == 0
    out = capsys.readouterr().out
    assert "No issues in the current sprint (Sprint 7) for an-org/a-repo." in out


def test_sprint_renders_table_with_items(monkeypatch, capsys) -> None:
    _stub_sprint(monkeypatch, viewer="me", items=[_sprint_item(1)])
    assert issues_cli.run(cli.parse_args(["issues", "--sprint"])) == 0
    out = capsys.readouterr().out
    assert "Sprint 7" in out
    assert "Item 1" in out


def test_sprint_markdown_format(monkeypatch, capsys) -> None:
    _stub_sprint(monkeypatch, viewer="me", items=[_sprint_item(1)])
    assert (
        issues_cli.run(cli.parse_args(["issues", "--sprint", "--format", "markdown"]))
        == 0
    )
    out = capsys.readouterr().out
    assert "## Sprint 7 · current sprint" in out
    assert "[#1](https://github.com/an-org/a-repo/issues/1)" in out


def test_sprint_show_key_prints_sprint_key(monkeypatch, capsys) -> None:
    _stub_sprint(monkeypatch, viewer="me", items=[_sprint_item(1)])
    assert issues_cli.run(cli.parse_args(["issues", "--sprint", "--show-key"])) == 0
    out = capsys.readouterr().out
    assert "Key" in out
    assert "someone else's / unassigned row" in out


class _StubbornWrapper(io.TextIOWrapper):
    """A stream that refuses an encoding change but accepts an error-handler one."""

    def reconfigure(self, **kwargs: Any) -> None:  # type: ignore[override]
        if "encoding" in kwargs:
            raise ValueError("encoding is fixed")
        super().reconfigure(**kwargs)


def test_tolerate_unencodable_switches_stream_to_utf8() -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    with pytest.raises(UnicodeEncodeError):
        stream.write("⚠")
    cli.tolerate_unencodable(stream)
    stream.write("ok ⚠ ✓")
    stream.flush()
    assert raw.getvalue() == "ok ⚠ ✓".encode()


def test_tolerate_unencodable_falls_back_to_replacement() -> None:
    raw = io.BytesIO()
    stream = _StubbornWrapper(raw, encoding="cp1252")
    cli.tolerate_unencodable(stream)
    stream.write("ok ⚠")
    stream.flush()
    assert raw.getvalue() == b"ok ?"


def test_tolerate_unencodable_ignores_streams_without_reconfigure() -> None:
    cli.tolerate_unencodable(io.StringIO())  # no reconfigure(); must not raise


def test_main_survives_cp1252_stdout_and_stderr(monkeypatch) -> None:
    """Goes through main(): fails if the reconfigure calls are dropped."""
    out_raw, err_raw = io.BytesIO(), io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out_raw, encoding="cp1252"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(err_raw, encoding="cp1252"))

    def glyphs(args: Any) -> int:
        print("✓ 🚀")
        print("⚠", file=sys.stderr)
        return 0

    monkeypatch.setitem(cli._COMMANDS, "issues", glyphs)
    assert cli.main(["issues"]) == 0
    sys.stdout.flush()
    sys.stderr.flush()
    assert out_raw.getvalue() == "✓ 🚀\n".encode()
    assert err_raw.getvalue() == "⚠\n".encode()


def _install_command(monkeypatch, exc: BaseException) -> None:
    def boom(args: Any) -> int:
        raise exc

    monkeypatch.setitem(cli._COMMANDS, "issues", boom)


def test_keyboard_interrupt_exits_130_without_traceback(monkeypatch, capsys) -> None:
    _install_command(monkeypatch, KeyboardInterrupt())
    assert cli.main(["issues"]) == 130
    captured = capsys.readouterr()
    assert "Interrupted." in captured.err
    assert "Traceback" not in captured.err


def test_broken_pipe_returns_141_when_stdout_has_no_fd(monkeypatch, capsys) -> None:
    _install_command(monkeypatch, BrokenPipeError())
    monkeypatch.setattr(cli.os, "open", lambda *a: pytest.fail("opened devnull"))
    assert cli.main(["issues"]) == 141
    assert capsys.readouterr().err == ""


def test_broken_pipe_is_silent_end_to_end() -> None:
    """Real pipe: reader closes early; the process must exit 141 with no stderr."""
    import subprocess
    from pathlib import Path

    code = (
        "import sys; from plate import cli\n"
        "def spew(a):\n"
        "    for _ in range(20000): print('x' * 100)\n"
        "    return 0\n"
        "cli._COMMANDS['issues'] = spew\n"
        "sys.exit(cli.main(['issues']))\n"
    )
    src = str(Path(__file__).resolve().parent.parent / "src")
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": src},
    )
    assert proc.stdout is not None and proc.stderr is not None
    proc.stdout.readline()
    proc.stdout.close()
    stderr = proc.stderr.read()
    assert proc.wait(timeout=30) == 141
    assert stderr == b""


class _Tty:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.mark.parametrize(
    ("mode", "env", "tty", "expected"),
    [
        ("always", {}, False, True),
        ("never", {}, True, False),
        ("auto", {}, True, True),
        ("auto", {}, False, False),
        ("auto", {"NO_COLOR": "1"}, True, False),
        ("auto", {"NO_COLOR": ""}, True, True),
        ("auto", {"FORCE_COLOR": "1"}, False, True),
        ("auto", {"FORCE_COLOR": ""}, False, True),
        ("auto", {"FORCE_COLOR": "0"}, True, False),
        ("auto", {"FORCE_COLOR": "false"}, True, False),
        ("auto", {"NO_COLOR": "1", "FORCE_COLOR": "1"}, True, False),
        ("always", {"NO_COLOR": "1"}, False, True),
        ("never", {"FORCE_COLOR": "1"}, True, False),
        ("auto", {"TERM": "xterm-256color"}, True, True),
        ("auto", {"TERM": "dumb"}, True, False),
        ("auto", {"TERM": "dumb"}, False, False),
        ("always", {"TERM": "dumb"}, True, True),
        ("auto", {"TERM": "dumb", "FORCE_COLOR": "1"}, True, True),
        ("auto", {"TERM": "dumb", "NO_COLOR": "1"}, True, False),
    ],
)
def test_color_enabled_resolution(monkeypatch, mode, env, tty, expected) -> None:
    from types import SimpleNamespace

    from plate.core import render

    # Cleared, not merely unset in the case table: the developer's own TERM
    # (and colour vars) must not reach the resolver.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(render, "sys", SimpleNamespace(stdout=_Tty(tty)))
    assert render.color_enabled(mode) is expected
