"""Tests for plate.retro.cli and plate.retro.github — flags, dispatch, and
the events fetch, with ``gh`` stubbed at the shared chokepoint."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from plate import cli
from plate.core import gh
from plate.core.gh import PlateError
from plate.retro import github
from plate.retro import model as retro_model


def _event(days_note: str = "2026-06-19T10:00:00Z") -> dict[str, Any]:
    return {"type": "PushEvent", "created_at": days_note, "payload": {}}


# --- fetch ---------------------------------------------------------------------


def _paged_run(pages: list[list[dict[str, Any]]]) -> tuple[Any, list[list[str]]]:
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        batch = pages[len(calls) - 1] if len(calls) <= len(pages) else []
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(batch), stderr="")

    return fake_run, calls


def test_fetch_stops_after_the_first_short_page(monkeypatch) -> None:
    fake_run, calls = _paged_run([[_event()] * 3])
    monkeypatch.setattr(gh, "run_command", fake_run)
    events = github.fetch_events("simon")
    assert len(events) == 3
    assert len(calls) == 1
    assert "users/simon/events?per_page=100&page=1" in calls[0][-1]


def test_fetch_walks_full_pages_up_to_the_cap(monkeypatch) -> None:
    full = [_event()] * github.EVENTS_PER_PAGE
    fake_run, calls = _paged_run([full, full, full])
    monkeypatch.setattr(gh, "run_command", fake_run)
    events = github.fetch_events("simon")
    assert len(events) == github.EVENTS_FEED_CAP
    # never asks for a page past the cap — that is a hard API error
    assert len(calls) == github.EVENTS_MAX_PAGES


def test_fetch_gh_failure_raises(monkeypatch) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(PlateError, match="authenticated"):
        github.fetch_events("simon")


def test_fetch_malformed_json_raises(monkeypatch) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="{not json", stderr="")

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(PlateError, match="Could not parse"):
        github.fetch_events("simon")


def test_fetch_non_list_payload_raises(monkeypatch) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 0, stdout='{"message": "x"}', stderr=""
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(PlateError, match="unexpected events payload"):
        github.fetch_events("simon")


# --- flags ---------------------------------------------------------------------


def test_defaults_when_not_given() -> None:
    args = cli.parse_args(["retro"])
    assert args.days == retro_model.DEFAULT_DAYS
    assert args.format == "terminal"
    assert args.color == "auto"


@pytest.mark.parametrize("days", ["6", "31", "0", "-3"])
def test_days_outside_range_rejected(days: str, capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["retro", "--days", days])
    assert excinfo.value.code == 2
    assert "between 7 and 30" in capsys.readouterr().err


@pytest.mark.parametrize("days", ["7", "30"])
def test_days_bounds_accepted(days: str) -> None:
    assert cli.parse_args(["retro", "--days", days]).days == int(days)


# --- run() ---------------------------------------------------------------------


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict[str, Any]],
    login: str | None = "simon",
) -> dict[str, Any]:
    calls: dict[str, Any] = {}
    monkeypatch.setattr(gh, "current_login", lambda: login)

    def fake_fetch(fetch_login: str) -> list[dict[str, Any]]:
        calls["login"] = fetch_login
        return events

    monkeypatch.setattr(github, "fetch_events", fake_fetch)
    return calls


def test_run_renders_the_panel(monkeypatch, capsys) -> None:
    calls = _stub(monkeypatch, [])
    assert cli.main(["retro", "--color", "never"]) == 0
    out = capsys.readouterr().out
    assert "── you · last 14 days " in out
    assert "reviews" in out
    assert "pushes" in out
    assert "opened" in out
    assert calls["login"] == "simon"


def test_run_days_reaches_the_window(monkeypatch, capsys) -> None:
    _stub(monkeypatch, [])
    assert cli.main(["retro", "--days", "7", "--color", "never"]) == 0
    assert "── you · last 7 days " in capsys.readouterr().out


def test_run_markdown(monkeypatch, capsys) -> None:
    _stub(monkeypatch, [])
    assert cli.main(["retro", "--format", "markdown"]) == 0
    out = capsys.readouterr().out
    assert "| Channel | Total | Last |" in out
    assert "you · last" not in out


def test_run_never_touches_repo_resolution(monkeypatch, capsys) -> None:
    def boom() -> str:
        raise AssertionError("retro must not resolve a repo")

    monkeypatch.setattr(gh, "current_repo", boom)
    _stub(monkeypatch, [])
    assert cli.main(["retro", "--color", "never"]) == 0


def test_run_missing_login_errors(monkeypatch, capsys) -> None:
    _stub(monkeypatch, [], login=None)
    assert cli.main(["retro"]) == 1
    assert "GitHub login" in capsys.readouterr().err


def test_run_warns_when_the_feed_cannot_cover_the_window(
    monkeypatch, capsys
) -> None:
    from datetime import UTC, datetime

    fresh = datetime.now(UTC).isoformat()
    _stub(monkeypatch, [_event(fresh)] * github.EVENTS_FEED_CAP)
    assert cli.main(["retro", "--color", "never"]) == 0
    err = capsys.readouterr().err
    assert "most recent events" in err


def test_run_no_warning_when_the_feed_is_short(monkeypatch, capsys) -> None:
    _stub(monkeypatch, [_event()])
    assert cli.main(["retro", "--color", "never"]) == 0
    assert "Note:" not in capsys.readouterr().err


def test_fetch_failure_surfaces_as_clean_exit(monkeypatch, capsys) -> None:
    monkeypatch.setattr(gh, "current_login", lambda: "simon")

    def fake_fetch(login: str) -> list[dict[str, Any]]:
        raise PlateError("gh failed to fetch your activity feed")

    monkeypatch.setattr(github, "fetch_events", fake_fetch)
    assert cli.main(["retro"]) == 1
    assert "activity feed" in capsys.readouterr().err
