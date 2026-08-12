"""Tests for plate.retro.cli and plate.retro.github — flags, dispatch, and
the activity fetches, with ``gh`` stubbed at the shared chokepoint."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from plate import cli
from plate.core import gh
from plate.core.gh import PlateError
from plate.retro import github
from plate.retro import model as retro_model


def _iso(days_ago: int = 0) -> str:
    stamp = datetime.now(UTC) - timedelta(days=days_ago)
    return stamp.isoformat().replace("+00:00", "Z")


def pr_item(repo: str = "acme/widget") -> dict[str, Any]:
    return {
        "repository_url": f"https://api.github.com/repos/{repo}",
        "created_at": _iso(),
    }


def closed_item(repo: str = "acme/widget") -> dict[str, Any]:
    return {
        "repository_url": f"https://api.github.com/repos/{repo}",
        "closed_at": _iso(),
    }


def review_event(repo: str = "SimAin/toy") -> dict[str, Any]:
    return {
        "type": "PullRequestReviewEvent",
        "repo": {"name": repo},
        "created_at": _iso(),
    }


def push_event(repo: str = "acme/widget") -> dict[str, Any]:
    return {
        "type": "PushEvent",
        "repo": {"name": repo},
        "created_at": _iso(),
        "payload": {"ref": "refs/heads/feat/x", "before": "a" * 8, "head": "b" * 8},
    }


def compare_payload(login: str = "simon") -> str:
    return json.dumps(
        {
            "total_commits": 1,
            "commits": [
                {
                    "sha": "c" * 8,
                    "author": {"login": login},
                    "commit": {"committer": {"date": _iso()}},
                }
            ],
        }
    )


# --- fetch ---------------------------------------------------------------------


def _paged_run(responses: list[str]) -> tuple[Any, list[str]]:
    paths: list[str] = []

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        paths.append(args[-1])
        body = responses[len(paths) - 1] if len(paths) <= len(responses) else "[]"
        return subprocess.CompletedProcess(args, 0, stdout=body, stderr="")

    return fake_run, paths


def test_fetch_events_stops_after_the_first_short_page(monkeypatch) -> None:
    fake_run, paths = _paged_run([json.dumps([review_event()] * 3)])
    monkeypatch.setattr(gh, "run_command", fake_run)
    events = github.fetch_events("simon")
    assert len(events) == 3
    assert paths == ["users/simon/events?per_page=100&page=1"]


def test_fetch_events_walks_full_pages_up_to_the_cap(monkeypatch) -> None:
    full = json.dumps([review_event()] * github.EVENTS_PER_PAGE)
    fake_run, paths = _paged_run([full, full, full])
    monkeypatch.setattr(gh, "run_command", fake_run)
    events = github.fetch_events("simon")
    assert len(events) == github.EVENTS_FEED_CAP
    # never asks for a page past the cap — that is a hard API error
    assert len(paths) == github.EVENTS_MAX_PAGES


def test_fetch_opened_builds_the_search_and_reports_totals(monkeypatch) -> None:
    body = json.dumps({"total_count": 1, "items": [pr_item()]})
    fake_run, paths = _paged_run([body])
    monkeypatch.setattr(gh, "run_command", fake_run)
    items, total = github.fetch_opened("simon", "2026-06-06")
    assert (len(items), total) == (1, 1)
    assert paths[0].startswith(
        "search/issues?q=author:simon+is:pr+created:>=2026-06-06"
    )


def test_fetch_closed_builds_the_search_and_reports_totals(monkeypatch) -> None:
    body = json.dumps({"total_count": 1, "items": [closed_item()]})
    fake_run, paths = _paged_run([body])
    monkeypatch.setattr(gh, "run_command", fake_run)
    items, total = github.fetch_closed("simon", "2026-06-06")
    assert (len(items), total) == (1, 1)
    assert paths[0].startswith(
        "search/issues?q=author:simon+is:pr+is:closed+closed:>=2026-06-06"
    )


def test_search_pagination_stops_on_a_short_page(monkeypatch) -> None:
    full_page = {"total_count": 150, "items": [pr_item()] * 100}
    short_page = {"total_count": 150, "items": [pr_item()] * 50}
    fake_run, paths = _paged_run([json.dumps(full_page), json.dumps(short_page)])
    monkeypatch.setattr(gh, "run_command", fake_run)
    items, total = github.fetch_opened("simon", "2026-06-06")
    assert len(items) == 150
    assert total == 150
    assert len(paths) == 2


def test_search_total_survives_a_page_missing_total_count(monkeypatch) -> None:
    full_page = json.dumps({"total_count": 150, "items": [pr_item()] * 100})
    bare_page = json.dumps({"items": [pr_item()] * 50})
    fake_run, _ = _paged_run([full_page, bare_page])
    monkeypatch.setattr(gh, "run_command", fake_run)
    _, total = github.fetch_opened("simon", "2026-06-06")
    assert total == 150


def test_fetch_compares_align_with_their_ranges(monkeypatch) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        path = args[-1]
        if "acme/widget" in path:
            return subprocess.CompletedProcess(
                args, 0, stdout=compare_payload(), stderr=""
            )
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="404")

    monkeypatch.setattr(gh, "run_command", fake_run)
    compares = github.fetch_compares(
        [
            ("acme/widget", "a" * 8, "b" * 8),
            ("acme/gone", "x" * 8, "y" * 8),
        ]
    )
    assert compares[0] is not None
    assert compares[0]["total_commits"] == 1
    assert compares[1] is None  # a failed compare falls back, never raises


def test_fetch_compares_empty_ranges_make_no_calls(monkeypatch) -> None:
    def boom(args: list[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError("no ranges means no requests")

    monkeypatch.setattr(gh, "run_command", boom)
    assert github.fetch_compares([]) == []


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


def test_fetch_unexpected_payloads_raise(monkeypatch) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 0, stdout='{"message": "x"}', stderr=""
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(PlateError, match="unexpected events payload"):
        github.fetch_events("simon")
    with pytest.raises(PlateError, match="unexpected search payload"):
        github.fetch_opened("simon", "2026-06-06")


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
    *,
    events: list[dict[str, Any]] | None = None,
    compares: list[dict[str, Any] | None] | None = None,
    prs: tuple[list[dict[str, Any]], int] = ([], 0),
    closed_prs: tuple[list[dict[str, Any]], int] = ([], 0),
    login: str | None = "simon",
) -> dict[str, Any]:
    calls: dict[str, Any] = {}
    monkeypatch.setattr(gh, "current_login", lambda: login)
    monkeypatch.setattr(github, "fetch_events", lambda lg: events or [])
    monkeypatch.setattr(github, "fetch_closed", lambda lg, since: closed_prs)

    def fake_compares(
        ranges: list[tuple[str, str, str]],
    ) -> list[dict[str, Any] | None]:
        calls["ranges"] = ranges
        return compares if compares is not None else [None] * len(ranges)

    monkeypatch.setattr(github, "fetch_compares", fake_compares)

    def fake_opened(
        fetch_login: str, since: str
    ) -> tuple[list[dict[str, Any]], int]:
        calls.update(login=fetch_login, since=since)
        return prs

    monkeypatch.setattr(github, "fetch_opened", fake_opened)
    return calls


def test_run_renders_one_panel_per_owner(monkeypatch, capsys) -> None:
    calls = _stub(
        monkeypatch,
        events=[push_event("acme/widget"), review_event("SimAin/toy")],
        compares=[json.loads(compare_payload())],
    )
    assert cli.main(["retro", "--color", "never"]) == 0
    out = capsys.readouterr().out
    assert "── acme · last 14 days " in out
    assert "── SimAin · last 14 days " in out
    assert calls["login"] == "simon"
    assert len(calls["since"]) == 10  # YYYY-MM-DD
    assert calls["ranges"] == [("acme/widget", "a" * 8, "b" * 8)]


def test_run_days_reaches_the_window(monkeypatch, capsys) -> None:
    _stub(monkeypatch, events=[review_event()])
    assert cli.main(["retro", "--days", "7", "--color", "never"]) == 0
    assert "· last 7 days " in capsys.readouterr().out


def test_run_empty_message(monkeypatch, capsys) -> None:
    _stub(monkeypatch)
    assert cli.main(["retro", "--color", "never"]) == 0
    assert "No activity found in the last 14 days." in capsys.readouterr().out


def test_run_markdown(monkeypatch, capsys) -> None:
    _stub(monkeypatch, events=[review_event("acme/widget")])
    assert cli.main(["retro", "--format", "markdown"]) == 0
    out = capsys.readouterr().out
    assert "## acme" in out
    assert "| Channel | Total | Last |" in out
    assert "last 14 days" not in out


def test_run_never_touches_repo_resolution(monkeypatch, capsys) -> None:
    def boom() -> str:
        raise AssertionError("retro must not resolve a repo")

    monkeypatch.setattr(gh, "current_repo", boom)
    _stub(monkeypatch, events=[review_event()])
    assert cli.main(["retro", "--color", "never"]) == 0


def test_run_missing_login_errors(monkeypatch, capsys) -> None:
    _stub(monkeypatch, login=None)
    assert cli.main(["retro"]) == 1
    assert "GitHub login" in capsys.readouterr().err


def test_run_warns_when_search_was_truncated(monkeypatch, capsys) -> None:
    _stub(monkeypatch, prs=([pr_item()], 1500))
    assert cli.main(["retro", "--color", "never"]) == 0
    assert "counting 1 of 1500 PRs opened" in capsys.readouterr().err


def test_run_warns_when_closed_search_was_truncated(monkeypatch, capsys) -> None:
    _stub(monkeypatch, closed_prs=([closed_item()], 1500))
    assert cli.main(["retro", "--color", "never"]) == 0
    assert "counting 1 of 1500 PRs closed" in capsys.readouterr().err


def test_run_warns_when_the_feed_cannot_cover_the_window(
    monkeypatch, capsys
) -> None:
    _stub(monkeypatch, events=[review_event()] * github.EVENTS_FEED_CAP)
    assert cli.main(["retro", "--color", "never"]) == 0
    err = capsys.readouterr().err
    assert "review and commit counts" in err


def test_run_warns_when_pushes_could_not_be_expanded(monkeypatch, capsys) -> None:
    _stub(monkeypatch, events=[push_event()], compares=[None])
    assert cli.main(["retro", "--color", "never"]) == 0
    err = capsys.readouterr().err
    assert "could not be expanded" in err


def test_run_no_warnings_when_everything_is_covered(monkeypatch, capsys) -> None:
    _stub(
        monkeypatch,
        events=[push_event()],
        compares=[json.loads(compare_payload())],
        prs=([pr_item()], 1),
    )
    assert cli.main(["retro", "--color", "never"]) == 0
    assert "Note:" not in capsys.readouterr().err


def test_fetch_failure_surfaces_as_clean_exit(monkeypatch, capsys) -> None:
    monkeypatch.setattr(gh, "current_login", lambda: "simon")

    def fake_events(login: str) -> list[dict[str, Any]]:
        raise PlateError("gh failed to fetch your activity")

    monkeypatch.setattr(github, "fetch_events", fake_events)
    assert cli.main(["retro"]) == 1
    assert "activity" in capsys.readouterr().err
