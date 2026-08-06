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

    def fake_fetch(
        repo: str, limit: int, *, timeline: bool = False
    ) -> tuple[str | None, list[dict[str, Any]]]:
        calls.update(repo=repo, limit=limit, timeline=timeline)
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


def test_owner_and_repo_are_mutually_exclusive(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["prs", "--owner", "an-org", "--repo", "an-org/a-repo"])
    assert excinfo.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


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
    def fake_fetch(
        repo: str, limit: int, *, timeline: bool = False
    ) -> tuple[str | None, list[dict[str, Any]]]:
        raise PlateError("gh failed to fetch open PRs for acme/widget:\nboom")

    monkeypatch.setattr(github, "fetch_prs_and_viewer", fake_fetch)
    assert cli.main(["prs", "--repo", "acme/widget"]) == 1


# --- owner view (--owner) ------------------------------------------------------


def _owner_pr(
    number: int,
    title: str = "A change",
    *,
    repo: str = "an-org/repo-a",
    author: str | None = "someone",
    assignees: list[str] | None = None,
    review_decision: str | None = None,
) -> dict[str, Any]:
    """A minimal owner-search PR payload (carries its own repository)."""
    return {
        **_pr(
            number,
            title,
            author=author,
            assignees=assignees,
            review_decision=review_decision,
        ),
        "repository": {"nameWithOwner": repo},
    }


def _boom_current_repo() -> str:
    raise AssertionError("current_repo() must not be called on the --owner path")


def _stub_owner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prs: list[dict[str, Any]],
    total: int,
    cfg: Any = None,
    owner_type: str = "organization",
    login: str = "me",
) -> dict[str, Any]:
    """Wire the owner path's I/O to in-memory stubs; record fetch arguments."""
    from plate.core import config, gh

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
        return prs, total

    monkeypatch.setattr(github, "fetch_owner_prs", fake_fetch)
    return calls


def test_mine_without_owner_errors() -> None:
    with pytest.raises(PlateError) as excinfo:
        prs_cli.run(cli.parse_args(["prs", "--mine"]))
    assert "--mine only applies with --owner" in str(excinfo.value)


def test_owner_flow_never_calls_current_repo(monkeypatch, capsys) -> None:
    _stub_owner(monkeypatch, prs=[_owner_pr(1)], total=1)
    # _boom_current_repo would raise if the owner path touched it.
    assert prs_cli.run(cli.parse_args(["prs", "--owner", "an-org"])) == 0
    assert "repo-a" in capsys.readouterr().out


def test_owner_groups_output_by_repo(monkeypatch, capsys) -> None:
    prs = [
        _owner_pr(1, "Fresh one", repo="an-org/repo-b"),
        _owner_pr(2, "Old one", repo="an-org/repo-a"),
    ]
    _stub_owner(monkeypatch, prs=prs, total=2)
    assert prs_cli.run(
        cli.parse_args(["prs", "--owner", "an-org", "--color", "never"])
    ) == 0
    out = capsys.readouterr().out
    assert "── an-org/repo-b · 1 open" in out
    assert "── an-org/repo-a · 1 open" in out
    assert out.index("repo-b") < out.index("repo-a")  # fetch order kept


def test_owner_alias_resolves_and_shows_arrow(monkeypatch, capsys) -> None:
    from plate.core import config

    cfg = config.Config(owners={"work": "company-org"})
    calls = _stub_owner(monkeypatch, prs=[_owner_pr(1)], total=1, cfg=cfg)
    assert prs_cli.run(cli.parse_args(["prs", "--owner", "work"])) == 0
    assert calls["owner"] == "company-org"  # fetch got the resolved name
    assert "work → company-org" in capsys.readouterr().out


def test_owner_literal_shows_no_arrow(monkeypatch, capsys) -> None:
    calls = _stub_owner(monkeypatch, prs=[_owner_pr(1)], total=1)
    assert prs_cli.run(cli.parse_args(["prs", "--owner", "an-org"])) == 0
    assert calls["owner"] == "an-org"
    assert "→" not in capsys.readouterr().out


def test_owner_mine_flag_reaches_the_fetch(monkeypatch, capsys) -> None:
    calls = _stub_owner(monkeypatch, prs=[_owner_pr(1)], total=1)
    assert prs_cli.run(
        cli.parse_args(["prs", "--owner", "an-org", "--mine"])
    ) == 0
    assert calls["mine"] is True


def test_owner_type_failure_lists_aliases(monkeypatch, capsys) -> None:
    from plate.core import config, gh

    cfg = config.Config(owners={"work": "company-org", "personal": "my-org"})
    _stub_owner(monkeypatch, prs=[], total=0, cfg=cfg)

    def fail(owner: str) -> str:
        raise PlateError(f"GitHub owner '{owner}' not found or not accessible.")

    monkeypatch.setattr(gh, "resolve_owner_type", fail)
    with pytest.raises(PlateError) as excinfo:
        prs_cli.run(cli.parse_args(["prs", "--owner", "typo"]))
    message = str(excinfo.value)
    assert "Configured aliases:" in message
    assert "work → company-org" in message
    assert "personal → my-org" in message


def test_owner_type_failure_without_aliases_has_no_alias_line(monkeypatch) -> None:
    from plate.core import gh

    _stub_owner(monkeypatch, prs=[], total=0)

    def fail(owner: str) -> str:
        raise PlateError(f"GitHub owner '{owner}' not found or not accessible.")

    monkeypatch.setattr(gh, "resolve_owner_type", fail)
    with pytest.raises(PlateError) as excinfo:
        prs_cli.run(cli.parse_args(["prs", "--owner", "nope"]))
    assert "Configured aliases:" not in str(excinfo.value)


def test_owner_missing_login_errors(monkeypatch) -> None:
    from plate.core import config, gh

    monkeypatch.setattr(config, "load_config", lambda *a, **k: config.Config())
    monkeypatch.setattr(gh, "current_login", lambda: None)
    with pytest.raises(PlateError) as excinfo:
        prs_cli.run(cli.parse_args(["prs", "--owner", "an-org"]))
    assert "GitHub login" in str(excinfo.value)


def test_owner_empty_default_message(monkeypatch, capsys) -> None:
    _stub_owner(monkeypatch, prs=[], total=0)
    assert prs_cli.run(cli.parse_args(["prs", "--owner", "an-org"])) == 0
    assert "No open PRs found for an-org." in capsys.readouterr().out


def test_owner_empty_mine_message(monkeypatch, capsys) -> None:
    calls = _stub_owner(monkeypatch, prs=[], total=0)
    assert prs_cli.run(
        cli.parse_args(["prs", "--owner", "an-org", "--mine"])
    ) == 0
    assert calls["mine"] is True
    assert "No open PRs authored by you for an-org." in capsys.readouterr().out


def test_owner_truncation_note_limit_hit(monkeypatch, capsys) -> None:
    prs = [_owner_pr(n) for n in range(1, 3)]
    _stub_owner(monkeypatch, prs=prs, total=5)
    assert prs_cli.run(
        cli.parse_args(["prs", "--owner", "an-org", "--limit", "2"])
    ) == 0
    err = capsys.readouterr().err
    assert "showing 2 of 5 open PRs for an-org (--limit 2)." in err


def test_owner_truncation_note_search_ceiling(monkeypatch, capsys) -> None:
    prs = [_owner_pr(n) for n in range(1, 4)]
    _stub_owner(monkeypatch, prs=prs, total=1500)
    assert prs_cli.run(cli.parse_args(["prs", "--owner", "an-org"])) == 0
    err = capsys.readouterr().err
    assert "at most 1000 results per query" in err
    assert "showing 3 of 1500 open PRs for an-org" in err
    assert "Use --mine or --repo to narrow." in err


def test_owner_no_note_when_complete(monkeypatch, capsys) -> None:
    prs = [_owner_pr(n) for n in range(1, 3)]
    _stub_owner(monkeypatch, prs=prs, total=2)
    assert prs_cli.run(cli.parse_args(["prs", "--owner", "an-org"])) == 0
    assert "Note:" not in capsys.readouterr().err


def test_owner_markdown_format(monkeypatch, capsys) -> None:
    _stub_owner(monkeypatch, prs=[_owner_pr(1)], total=1)
    assert prs_cli.run(
        cli.parse_args(["prs", "--owner", "an-org", "--format", "markdown"])
    ) == 0
    out = capsys.readouterr().out
    assert "## an-org/repo-a" in out
    assert "| PR ID | Title |" in out


def test_owner_show_key_prints_owner_key(monkeypatch, capsys) -> None:
    _stub_owner(monkeypatch, prs=[_owner_pr(1)], total=1)
    assert prs_cli.run(
        cli.parse_args(["prs", "--owner", "an-org", "--show-key", "--color", "never"])
    ) == 0
    out = capsys.readouterr().out
    assert "Key" in out
    assert "grouped by repository" in out


def test_owner_summary_line_present(monkeypatch, capsys) -> None:
    prs = [
        _owner_pr(1, "Mine", author="me"),
        _owner_pr(2, "Review this", author="alice"),
    ]
    _stub_owner(monkeypatch, prs=prs, total=2)
    assert prs_cli.run(
        cli.parse_args(["prs", "--owner", "an-org", "--color", "never"])
    ) == 0
    out = capsys.readouterr().out
    assert "2 open" in out
    assert "1 to review" in out


# --- timeline (--timeline) ------------------------------------------------------


def test_timeline_flag_reaches_fetch_and_output(monkeypatch, capsys) -> None:
    calls = _stub_fetch(
        monkeypatch, prs=[_pr(1, "Mine", author="simon")], login="simon"
    )
    assert (
        cli.main(["prs", "--repo", "acme/widget", "--timeline", "--color", "never"])
        == 0
    )
    assert calls["timeline"] is True
    assert "↳" in capsys.readouterr().out


def test_timeline_off_by_default(monkeypatch, capsys) -> None:
    calls = _stub_fetch(
        monkeypatch, prs=[_pr(1, "Mine", author="simon")], login="simon"
    )
    assert cli.main(["prs", "--repo", "acme/widget", "--color", "never"]) == 0
    assert calls["timeline"] is False
    assert "↳" not in capsys.readouterr().out


def test_timeline_ignored_for_markdown(monkeypatch, capsys) -> None:
    calls = _stub_fetch(
        monkeypatch, prs=[_pr(1, "Mine", author="simon")], login="simon"
    )
    assert (
        cli.main(
            ["prs", "--repo", "acme/widget", "--timeline", "--format", "markdown"]
        )
        == 0
    )
    assert calls["timeline"] is False
    assert "↳" not in capsys.readouterr().out


def test_timeline_with_owner_errors() -> None:
    with pytest.raises(PlateError) as excinfo:
        prs_cli.run(cli.parse_args(["prs", "--owner", "an-org", "--timeline"]))
    assert "only available in the repo view" in str(excinfo.value)


def test_show_key_teaches_strip_with_timeline(monkeypatch, capsys) -> None:
    _stub_fetch(monkeypatch, prs=[_pr(1, "Mine", author="simon")], login="simon")
    assert (
        cli.main(
            [
                "prs",
                "--repo",
                "acme/widget",
                "--timeline",
                "--show-key",
                "--color",
                "never",
            ]
        )
        == 0
    )
    assert "Strip" in capsys.readouterr().out
