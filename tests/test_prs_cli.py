"""Tests for plate.prs.cli (the ``prs`` subcommand: flags + run() dispatch),
exercised end-to-end through plate.cli.main(["prs", ...]) with the fetch layer
stubbed — same style as tests/test_cli.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from plate import cli
from plate.core.gh import PlateError
from plate.prs import cli as prs_cli
from plate.prs import github


def _pr(
    number: int,
    title: str = "A change",
    *,
    assignees: list[str] | None = None,
    is_draft: bool = False,
    review_decision: str | None = None,
    comments: int = 0,
    author: str | None = None,
    rollup: str | None = "SUCCESS",
) -> dict[str, Any]:
    """A minimal PR node in the GraphQL shape the fetch layer produces."""
    return {
        "number": number,
        "url": f"https://github.com/acme/widget/pull/{number}",
        "title": title,
        "isDraft": is_draft,
        "assignees": {"nodes": [{"login": login} for login in assignees or []]},
        "reviewDecision": review_decision,
        "latestReviews": {"nodes": []},
        "reviewRequests": {"nodes": []},
        "author": {"login": author, "__typename": "User"} if author else None,
        "updatedAt": "2024-01-01T00:00:00Z",
        "mergeable": "MERGEABLE",
        "totalCommentsCount": comments,
        "commits": {
            "nodes": [
                {"commit": {"statusCheckRollup": {"state": rollup} if rollup else None}}
            ]
        },
    }


def _stub_fetch(
    monkeypatch: pytest.MonkeyPatch, *, prs: list[dict[str, Any]], login: str | None
) -> dict[str, Any]:
    """Wire the prs path's fetch to an in-memory stub; record its arguments."""
    calls: dict[str, Any] = {}

    def fake_fetch(repo: str, limit: int) -> tuple[str | None, list[dict[str, Any]]]:
        calls.update(repo=repo, limit=limit)
        return login, prs

    monkeypatch.setattr(github, "fetch_prs_and_viewer", fake_fetch)
    return calls


# --- flags -------------------------------------------------------------------


def test_defaults_when_not_given() -> None:
    args = cli.parse_args(["prs"])
    assert args.limit == prs_cli.DEFAULT_LIMIT
    assert args.stale_days == prs_cli.DEFAULT_STALE_DAYS
    assert args.format == "terminal"
    assert args.color == "auto"


def test_limit_zero_is_rejected(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["prs", "--limit", "0"])
    assert excinfo.value.code == 2
    assert "positive integer" in capsys.readouterr().err


def test_limit_negative_is_rejected(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["prs", "--limit", "-5"])
    assert excinfo.value.code == 2
    assert "positive integer" in capsys.readouterr().err


def test_stale_days_zero_is_rejected(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["prs", "--stale-days", "0"])
    assert excinfo.value.code == 2
    assert "positive integer" in capsys.readouterr().err


def test_no_owner_flag() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["prs", "--owner", "an-org"])


# --- run() ---------------------------------------------------------------


def test_empty_message(monkeypatch, capsys) -> None:
    _stub_fetch(monkeypatch, prs=[], login="simon")
    assert cli.main(["prs", "--repo", "acme/widget"]) == 0
    assert "No open PRs found for acme/widget." in capsys.readouterr().out


def test_repo_defaults_to_current_repo(monkeypatch, capsys) -> None:
    from plate.core import gh

    monkeypatch.setattr(gh, "current_repo", lambda: "acme/widget")
    calls = _stub_fetch(monkeypatch, prs=[], login="simon")
    assert cli.main(["prs"]) == 0
    assert calls["repo"] == "acme/widget"


def test_grouping_order_in_output(monkeypatch, capsys) -> None:
    prs = [
        _pr(1, "Mine", author="simon"),
        _pr(2, "Needs my review", author="alice"),
        _pr(
            3,
            "Someone else's",
            author="bob",
            assignees=["carol"],
            review_decision="APPROVED",
        ),
    ]
    _stub_fetch(monkeypatch, prs=prs, login="simon")
    assert cli.main(["prs", "--repo", "acme/widget", "--color", "never"]) == 0
    out = capsys.readouterr().out

    yours_idx = out.index("── yours")
    review_idx = out.index("── to review")
    rest_idx = out.index("── the rest")
    assert yours_idx < review_idx < rest_idx
    mine_idx = out.index("Mine")
    review_pr_idx = out.index("Needs my review")
    rest_pr_idx = out.index("Someone else's")
    assert yours_idx < mine_idx < review_idx
    assert review_idx < review_pr_idx < rest_idx
    assert rest_idx < rest_pr_idx


def test_summary_line_present(monkeypatch, capsys) -> None:
    prs = [_pr(1, "Mine", author="simon"), _pr(2, "Review this", author="alice")]
    _stub_fetch(monkeypatch, prs=prs, login="simon")
    assert cli.main(["prs", "--repo", "acme/widget", "--color", "never"]) == 0
    out = capsys.readouterr().out
    assert "2 open" in out
    assert "1 to review" in out


def test_markdown_format(monkeypatch, capsys) -> None:
    prs = [_pr(1, "Mine", author="simon")]
    _stub_fetch(monkeypatch, prs=prs, login="simon")
    assert (
        cli.main(["prs", "--repo", "acme/widget", "--format", "markdown"]) == 0
    )
    out = capsys.readouterr().out
    assert "| PR ID | Title |" in out
    assert "#1" in out
    assert "yours" not in out  # markdown has no group dividers


def test_show_key_prints_key(monkeypatch, capsys) -> None:
    prs = [_pr(1, "Mine", author="simon")]
    _stub_fetch(monkeypatch, prs=prs, login="simon")
    assert (
        cli.main(["prs", "--repo", "acme/widget", "--show-key", "--color", "never"])
        == 0
    )
    out = capsys.readouterr().out
    assert "Key" in out
    assert "conflicts" in out


def test_limit_hit_note(monkeypatch, capsys) -> None:
    prs = [_pr(1, "Mine", author="simon")]
    _stub_fetch(monkeypatch, prs=prs, login="simon")
    assert (
        cli.main(["prs", "--repo", "acme/widget", "--limit", "1"]) == 0
    )
    err = capsys.readouterr().err
    assert "fetched 1 open PRs; there may be more not shown." in err


def test_no_limit_note_when_under_limit(monkeypatch, capsys) -> None:
    prs = [_pr(1, "Mine", author="simon")]
    _stub_fetch(monkeypatch, prs=prs, login="simon")
    assert cli.main(["prs", "--repo", "acme/widget", "--limit", "5"]) == 0
    assert "Note:" not in capsys.readouterr().err


def test_missing_login_note(monkeypatch, capsys) -> None:
    prs = [_pr(1, "Somebody's PR", author="alice")]
    _stub_fetch(monkeypatch, prs=prs, login=None)
    assert cli.main(["prs", "--repo", "acme/widget"]) == 0
    err = capsys.readouterr().err
    assert "could not be grouped into yours / to review" in err


def test_no_missing_login_note_when_login_known(monkeypatch, capsys) -> None:
    prs = [_pr(1, "Mine", author="simon")]
    _stub_fetch(monkeypatch, prs=prs, login="simon")
    assert cli.main(["prs", "--repo", "acme/widget"]) == 0
    assert "could not be grouped" not in capsys.readouterr().err


def test_gh_failure_surfaces_as_plate_error(monkeypatch) -> None:
    def fake_fetch(repo: str, limit: int) -> tuple[str | None, list[dict[str, Any]]]:
        raise PlateError("gh failed to fetch open PRs for acme/widget:\nboom")

    monkeypatch.setattr(github, "fetch_prs_and_viewer", fake_fetch)
    assert cli.main(["prs", "--repo", "acme/widget"]) == 1
