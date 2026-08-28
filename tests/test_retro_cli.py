"""Tests for plate.retro.cli and plate.retro.github — flags, dispatch, and
the activity fetches, with ``gh`` stubbed at the shared chokepoint."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from plate import cli
from plate.core import config, gh
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


def review_event(repo: str = "user/toy") -> dict[str, Any]:
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


def compare_payload(login: str = "user") -> str:
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
    events = github.fetch_events("user")
    assert len(events) == 3
    assert paths == ["users/user/events?per_page=100&page=1"]


def test_fetch_events_walks_full_pages_up_to_the_cap(monkeypatch) -> None:
    full = json.dumps([review_event()] * github.EVENTS_PER_PAGE)
    fake_run, paths = _paged_run([full, full, full])
    monkeypatch.setattr(gh, "run_command", fake_run)
    events = github.fetch_events("user")
    assert len(events) == github.EVENTS_FEED_CAP
    # never asks for a page past the cap — that is a hard API error
    assert len(paths) == github.EVENTS_MAX_PAGES


def test_fetch_opened_builds_the_search_and_reports_totals(monkeypatch) -> None:
    body = json.dumps({"total_count": 1, "items": [pr_item()]})
    fake_run, paths = _paged_run([body])
    monkeypatch.setattr(gh, "run_command", fake_run)
    items, total = github.fetch_opened("user", "2026-06-06")
    assert (len(items), total) == (1, 1)
    assert paths[0].startswith("search/issues?q=author:user+is:pr+created:>=2026-06-06")


def test_fetch_closed_builds_the_search_and_reports_totals(monkeypatch) -> None:
    body = json.dumps({"total_count": 1, "items": [closed_item()]})
    fake_run, paths = _paged_run([body])
    monkeypatch.setattr(gh, "run_command", fake_run)
    items, total = github.fetch_closed("user", "2026-06-06")
    assert (len(items), total) == (1, 1)
    assert paths[0].startswith(
        "search/issues?q=author:user+is:pr+is:closed+closed:>=2026-06-06"
    )


def test_search_pagination_stops_on_a_short_page(monkeypatch) -> None:
    full_page = {"total_count": 150, "items": [pr_item()] * 100}
    short_page = {"total_count": 150, "items": [pr_item()] * 50}
    fake_run, paths = _paged_run([json.dumps(full_page), json.dumps(short_page)])
    monkeypatch.setattr(gh, "run_command", fake_run)
    items, total = github.fetch_opened("user", "2026-06-06")
    assert len(items) == 150
    assert total == 150
    assert len(paths) == 2


def test_search_total_survives_a_page_missing_total_count(monkeypatch) -> None:
    full_page = json.dumps({"total_count": 150, "items": [pr_item()] * 100})
    bare_page = json.dumps({"items": [pr_item()] * 50})
    fake_run, _ = _paged_run([full_page, bare_page])
    monkeypatch.setattr(gh, "run_command", fake_run)
    _, total = github.fetch_opened("user", "2026-06-06")
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
    assert github.fetch_compares([None]) == [None]  # a created-only branch


def test_fetch_compares_skip_none_ranges_keeping_alignment(monkeypatch) -> None:
    fake_run, paths = _paged_run([compare_payload()])
    monkeypatch.setattr(gh, "run_command", fake_run)
    compares = github.fetch_compares([None, ("acme/widget", "a" * 8, "b" * 8)])
    assert compares[0] is None
    assert compares[1] is not None
    assert paths == ["repos/acme/widget/compare/aaaaaaaa...bbbbbbbb"]


def test_fetch_branch_commits_lists_your_commits_since_the_window(monkeypatch) -> None:
    listing = [{"sha": "c" * 8, "author": {"login": "user"}}]
    fake_run, paths = _paged_run([json.dumps(listing)])
    monkeypatch.setattr(gh, "run_command", fake_run)
    listings = github.fetch_branch_commits(
        [("acme/widget", "feat/x")], "user", "2026-06-06T00:00:00Z"
    )
    assert listings == [listing]
    assert paths == [
        "repos/acme/widget/commits?sha=feat%2Fx&author=user"
        "&since=2026-06-06T00:00:00Z&per_page=100&page=1"
    ]


def test_fetch_branch_commits_walks_full_pages(monkeypatch) -> None:
    full = [{"sha": str(n)} for n in range(github.COMMITS_PER_PAGE)]
    fake_run, paths = _paged_run([json.dumps(full), json.dumps(full[:1])])
    monkeypatch.setattr(gh, "run_command", fake_run)
    (listing,) = github.fetch_branch_commits(
        [("acme/widget", "main")], "user", "2026-06-06T00:00:00Z"
    )
    assert listing is not None and len(listing) == github.COMMITS_PER_PAGE + 1
    assert [path[-6:] for path in paths] == ["page=1", "page=2"]


def test_fetch_branch_commits_keep_earlier_pages_when_a_later_one_fails(
    monkeypatch,
) -> None:
    full = [{"sha": str(n)} for n in range(github.COMMITS_PER_PAGE)]

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[-1].endswith("page=1"):
            return subprocess.CompletedProcess(
                args, 0, stdout=json.dumps(full), stderr=""
            )
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="gh: HTTP 404")

    monkeypatch.setattr(gh, "run_command", fake_run)
    (listing,) = github.fetch_branch_commits(
        [("acme/widget", "main")], "user", "2026-06-06T00:00:00Z"
    )
    assert listing == full  # page 2 lost, page 1 kept; not a None fallback


def test_fetch_branch_commits_align_around_a_none_in_the_middle(monkeypatch) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        repo = args[-1].split("/")[1]
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps([{"sha": repo}]), stderr=""
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    listings = github.fetch_branch_commits(
        [("acme/a", "main"), None, ("acme/b", "main")], "user", "2026-06-06T00:00:00Z"
    )
    assert listings == [[{"sha": "acme"}], None, [{"sha": "acme"}]]


def test_fetch_branch_commits_none_for_missing_branches_and_non_branches(
    monkeypatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        assert "acme/gone" in args[-1]  # the None entry makes no request
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="404")

    monkeypatch.setattr(gh, "run_command", fake_run)
    listings = github.fetch_branch_commits(
        [("acme/gone", "feat/merged"), None], "user", "2026-06-06T00:00:00Z"
    )
    assert listings == [None, None]


def test_fetch_gh_failure_raises(monkeypatch) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(PlateError, match="authenticated"):
        github.fetch_events("user")


def test_fetch_rate_limit_failure_explains_itself(monkeypatch) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="gh: You have exceeded a secondary rate limit"
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(PlateError) as excinfo:
        github.fetch_events("user")

    message = str(excinfo.value)
    assert "secondary rate limit" in message  # the raw stderr is kept
    assert "GitHub is rate limiting this token" in message
    assert "--days" in message


def test_fetch_malformed_json_raises(monkeypatch) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="{not json", stderr="")

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(PlateError, match="Could not parse"):
        github.fetch_events("user")


def test_fetch_unexpected_payloads_raise(monkeypatch) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 0, stdout='{"message": "x"}', stderr=""
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(PlateError, match="unexpected events payload"):
        github.fetch_events("user")
    with pytest.raises(PlateError, match="unexpected search payload"):
        github.fetch_opened("user", "2026-06-06")


# --- transient-5xx retries (the same policy the search views use) -------------
#
# The sleep between attempts is patched out so these stay instant.


def _flaky_run(failures: int, body: str, stderr: str = "gh: HTTP 502") -> Any:
    """A fake ``run_command`` failing ``failures`` times before succeeding."""
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if len(calls) <= failures:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr=stderr)
        return subprocess.CompletedProcess(args, 0, stdout=body, stderr="")

    fake_run.calls = calls  # type: ignore[attr-defined]
    return fake_run


def test_fetch_retries_a_transient_failure(monkeypatch) -> None:
    fake_run = _flaky_run(1, json.dumps([review_event()]))
    monkeypatch.setattr(gh, "run_command", fake_run)
    monkeypatch.setattr(gh.time, "sleep", lambda seconds: None)

    assert len(github.fetch_events("user")) == 1
    assert len(fake_run.calls) == 2


def test_fetch_persistent_transient_failure_raises(monkeypatch) -> None:
    fake_run = _flaky_run(99, "")
    monkeypatch.setattr(gh, "run_command", fake_run)
    sleeps: list[float] = []
    monkeypatch.setattr(gh.time, "sleep", sleeps.append)

    with pytest.raises(PlateError) as excinfo:
        github.fetch_events("user")

    assert "HTTP 502" in str(excinfo.value)
    assert len(fake_run.calls) == gh.MAX_ATTEMPTS
    assert sleeps == [1.0, 2.0]  # backoff grows; nothing after the last attempt


def test_fetch_non_transient_failure_does_not_retry(monkeypatch) -> None:
    fake_run = _flaky_run(99, "", stderr="gh: HTTP 404 Not Found")
    monkeypatch.setattr(gh, "run_command", fake_run)

    with pytest.raises(PlateError, match="authenticated"):
        github.fetch_events("user")

    assert len(fake_run.calls) == 1


# --- the stderr progress line -------------------------------------------------


class _TtyStderr(io.StringIO):
    """A StringIO posing as a terminal, so ``gh.progress`` writes to it."""

    def isatty(self) -> bool:
        return True


def test_fetch_paints_progress_on_a_tty(monkeypatch) -> None:
    stderr = _TtyStderr()
    monkeypatch.setattr(sys, "stderr", stderr)
    fake_run, _ = _paged_run([json.dumps([review_event()])])
    monkeypatch.setattr(gh, "run_command", fake_run)

    github.fetch_events("user")

    assert "Fetching your GitHub events…" in stderr.getvalue()


def test_fetch_paints_retry_progress_on_a_tty(monkeypatch) -> None:
    stderr = _TtyStderr()
    monkeypatch.setattr(sys, "stderr", stderr)
    fake_run = _flaky_run(1, json.dumps([review_event()]))
    monkeypatch.setattr(gh, "run_command", fake_run)
    monkeypatch.setattr(gh.time, "sleep", lambda seconds: None)

    github.fetch_events("user")

    output = stderr.getvalue()
    assert "GitHub answered HTTP 502 — retrying (attempt 2/3)…" in output
    assert output.startswith("\r\x1b[2K")


def test_fetch_compares_paints_branch_progress_on_a_tty(monkeypatch) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout=compare_payload(), stderr="")

    monkeypatch.setattr(gh, "run_command", fake_run)

    stderr = _TtyStderr()
    monkeypatch.setattr(sys, "stderr", stderr)
    github.fetch_compares([("acme/widget", "a" * 8, "b" * 8)])
    assert "Expanding pushes on 1 branch…" in stderr.getvalue()

    stderr = _TtyStderr()
    monkeypatch.setattr(sys, "stderr", stderr)
    github.fetch_compares(
        [
            ("acme/widget", "a" * 8, "b" * 8),
            ("acme/other", "c" * 8, "d" * 8),
        ]
    )
    assert "Expanding pushes on 2 branches…" in stderr.getvalue()


def test_fetch_branch_commits_paints_progress_for_real_jobs_only(monkeypatch) -> None:
    fake_run, _ = _paged_run([json.dumps([])])
    monkeypatch.setattr(gh, "run_command", fake_run)
    stderr = _TtyStderr()
    monkeypatch.setattr(sys, "stderr", stderr)
    github.fetch_branch_commits(
        [("acme/widget", "feat/x"), None], "user", "2026-06-06T00:00:00Z"
    )
    assert "Listing commits on 1 branch…" in stderr.getvalue()


def test_fetch_is_silent_when_stderr_is_not_a_tty(monkeypatch) -> None:
    stderr = io.StringIO()  # isatty() is False
    monkeypatch.setattr(sys, "stderr", stderr)
    fake_run, _ = _paged_run(
        [
            json.dumps([review_event()]),
            json.dumps({"total_count": 1, "items": [pr_item()]}),
            compare_payload(),
        ]
    )
    monkeypatch.setattr(gh, "run_command", fake_run)

    github.fetch_events("user")
    github.fetch_opened("user", "2026-06-06")
    github.fetch_compares([("acme/widget", "a" * 8, "b" * 8)])

    assert stderr.getvalue() == ""


def test_run_clears_the_progress_line_before_rendering(monkeypatch) -> None:
    stderr = _TtyStderr()
    monkeypatch.setattr(sys, "stderr", stderr)
    _stub(monkeypatch, events=[review_event()])

    def painting_events(login: str) -> list[dict[str, Any]]:
        gh.progress("Searching PRs opened…")
        return [review_event()]

    monkeypatch.setattr(github, "fetch_events", painting_events)

    assert cli.main(["retro", "--color", "never"]) == 0
    assert stderr.getvalue().endswith("\r\x1b[2K")


def test_compare_retries_a_transient_failure(monkeypatch) -> None:
    fake_run = _flaky_run(1, compare_payload())
    monkeypatch.setattr(gh, "run_command", fake_run)
    monkeypatch.setattr(gh.time, "sleep", lambda seconds: None)

    compares = github.fetch_compares([("acme/widget", "a" * 8, "b" * 8)])

    assert compares[0] is not None
    assert len(fake_run.calls) == 2


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
    listings: list[list[dict[str, Any]] | None] | None = None,
    prs: tuple[list[dict[str, Any]], int] = ([], 0),
    closed_prs: tuple[list[dict[str, Any]], int] = ([], 0),
    login: str | None = "user",
) -> dict[str, Any]:
    calls: dict[str, Any] = {}
    monkeypatch.setattr(gh, "current_login", lambda: login)
    monkeypatch.setattr(github, "fetch_events", lambda lg: events or [])
    monkeypatch.setattr(github, "fetch_closed", lambda lg, since: closed_prs)

    def fake_compares(
        ranges: list[tuple[str, str, str] | None],
    ) -> list[dict[str, Any] | None]:
        calls["ranges"] = ranges
        return compares if compares is not None else [None] * len(ranges)

    monkeypatch.setattr(github, "fetch_compares", fake_compares)

    def fake_listings(
        branches: list[tuple[str, str] | None], fetch_login: str, since: str
    ) -> list[list[dict[str, Any]] | None]:
        calls.update(branches=branches, listing_since=since)
        return listings if listings is not None else [None] * len(branches)

    monkeypatch.setattr(github, "fetch_branch_commits", fake_listings)

    def fake_opened(fetch_login: str, since: str) -> tuple[list[dict[str, Any]], int]:
        calls.update(login=fetch_login, since=since)
        return prs

    monkeypatch.setattr(github, "fetch_opened", fake_opened)
    return calls


def test_run_renders_one_panel_per_owner(monkeypatch, capsys) -> None:
    calls = _stub(
        monkeypatch,
        events=[push_event("acme/widget"), review_event("user/toy")],
        compares=[json.loads(compare_payload())],
    )
    assert cli.main(["retro", "--color", "never"]) == 0
    out = capsys.readouterr().out
    assert "── acme · last 14 days " in out
    assert "── user · last 14 days " in out
    assert calls["login"] == "user"
    assert len(calls["since"]) == 10  # YYYY-MM-DD
    assert calls["ranges"] == [("acme/widget", "a" * 8, "b" * 8)]
    assert calls["branches"] == [("acme/widget", "feat/x")]
    assert calls["listing_since"].endswith("T00:00:00Z")  # midnight UTC, day one


def test_run_counts_a_created_branch_from_its_listing(monkeypatch, capsys) -> None:
    created = {
        "type": "CreateEvent",
        "repo": {"name": "acme/fresh"},
        "created_at": _iso(),
        "payload": {"ref": "main", "ref_type": "branch"},
    }
    calls = _stub(
        monkeypatch,
        events=[created],
        listings=[json.loads(compare_payload())["commits"]],
    )
    assert cli.main(["retro", "--color", "never"]) == 0
    out, err = capsys.readouterr()
    assert "── acme · last 14 days " in out
    assert "Note:" not in err
    assert calls["ranges"] == [None]  # nothing to compare
    assert calls["branches"] == [("acme/fresh", "main")]


def test_retro_does_not_read_config(monkeypatch, capsys) -> None:
    _stub(monkeypatch)

    def refuse(path: str | None = None) -> None:
        raise AssertionError("retro must not read config")

    monkeypatch.setattr(config, "load_config", refuse)
    assert cli.main(["retro", "--color", "never"]) == 0


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


def test_run_warns_when_the_feed_cannot_cover_the_window(monkeypatch, capsys) -> None:
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
    monkeypatch.setattr(gh, "current_login", lambda: "user")

    def fake_events(login: str) -> list[dict[str, Any]]:
        raise PlateError("gh failed to fetch your activity")

    monkeypatch.setattr(github, "fetch_events", fake_events)
    assert cli.main(["retro"]) == 1
    assert "activity" in capsys.readouterr().err
