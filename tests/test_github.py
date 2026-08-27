"""Tests for plate.core.gh (shared git/gh plumbing) and plate.issues.github
(issue-domain GraphQL fetches).

Most of this module is pure URL/query building and is exercised directly —
those tests never shell out. The handful that reach an I/O boundary function
(``_search_issues``, ``fetch_owner_issues``, ``resolve_owner_type``) monkeypatch
``gh.run_command``, the shared subprocess chokepoint in :mod:`plate.core.gh` —
which every domain fetch (in :mod:`plate.issues.github` and, later, a
``plate.prs.github``) calls through the same ``gh`` module reference, so this
one patch target intercepts all of them.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from typing import Any

import pytest

from plate.core import gh
from plate.issues import github


@pytest.mark.parametrize(
    "remote",
    [
        "git@github.com:an-org/a-repo.git",
        "git@github.com:an-org/a-repo",
        "https://github.com/an-org/a-repo.git",
        "https://github.com/an-org/a-repo",
        "https://github.com/an-org/a-repo/",
        "ssh://git@github.com/an-org/a-repo.git",
        "  git@github.com:an-org/a-repo.git\n",
    ],
)
def test_repo_from_remote_parses_github_urls(remote: str) -> None:
    assert gh.repo_from_remote(remote) == "an-org/a-repo"


def test_run_missing_binary_raises_issue_check_error() -> None:
    with pytest.raises(gh.PlateError, match="no-such-binary-xyz"):
        gh.run_command(["no-such-binary-xyz", "--version"])


def test_run_missing_binary_generic_install_hint() -> None:
    with pytest.raises(gh.PlateError) as excinfo:
        gh.run_command(["no-such-binary-xyz", "--version"])
    assert "Install no-such-binary-xyz and ensure it is on PATH" in str(excinfo.value)


def test_run_missing_gh_binary_hints_cli_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_missing(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(gh.subprocess, "run", raise_missing)
    with pytest.raises(gh.PlateError) as excinfo:
        gh.run_command(["gh", "api", "user"])
    message = str(excinfo.value)
    assert "'gh' is not installed" in message
    assert "https://cli.github.com" in message
    assert "gh auth login" in message


def test_sprint_filter_defaults_to_iteration() -> None:
    assert github.sprint_filter("Iteration") == "iteration:@current"
    assert github.sprint_filter("Sprint") == "sprint:@current"
    assert github.sprint_filter("") == "iteration:@current"


def test_sprint_query_uses_org_root_and_interpolates_fields() -> None:
    query = github._sprint_query("an-org", "organization", 2, "Iteration", "Status")
    assert 'organization(login: "an-org")' in query
    assert "projectV2(number: 2)" in query
    assert 'fieldValueByName(name: "Status")' in query
    assert 'fieldValueByName(name: "Iteration")' in query
    assert "query: $q" in query


def test_sprint_query_requests_viewer_login() -> None:
    # The sprint view groups yours/others by the concrete login — fetched as a
    # second root field in the same round trip, not via a gh api user call.
    query = github._sprint_query("an-org", "organization", 2, "Iteration", "Status")
    assert "viewer { login }" in query


def test_sprint_query_uses_user_root() -> None:
    query = github._sprint_query("a-user", "user", 5, "Sprint", "Column")
    assert 'user(login: "a-user")' in query
    assert 'fieldValueByName(name: "Column")' in query
    assert 'fieldValueByName(name: "Sprint")' in query


# --- board-field validation --------------------------------------------------


def _fields() -> list[dict[str, str]]:
    """A representative board: one iteration field, one single-select, one text."""
    return [
        {"name": "Sprint", "dataType": "ITERATION"},
        {"name": "Status", "dataType": "SINGLE_SELECT"},
        {"name": "Estimate", "dataType": "NUMBER"},
        {"name": "Notes", "dataType": "TEXT"},
    ]


def _fields_with_status_options() -> list[dict[str, Any]]:
    """A board whose Status field carries real (emoji-prefixed) options."""
    return [
        {"name": "Sprint", "dataType": "ITERATION"},
        {
            "name": "Status",
            "dataType": "SINGLE_SELECT",
            "options": [
                {"name": "🚀 Priority"},
                {"name": "In progress"},
                {"name": "Backlog"},
            ],
        },
    ]


def test_fields_query_uses_org_root_and_common_fragment() -> None:
    query = github._fields_query("an-org", "organization", 3)
    assert 'organization(login: "an-org")' in query
    assert "projectV2(number: 3)" in query
    assert "... on ProjectV2FieldCommon { name dataType }" in query


def test_fields_query_includes_single_select_options() -> None:
    query = github._fields_query("an-org", "organization", 3)
    assert "... on ProjectV2SingleSelectField { options { name } }" in query


def test_fields_query_uses_user_root() -> None:
    query = github._fields_query("a-user", "user", 7)
    assert 'user(login: "a-user")' in query


def test_validate_board_fields_accepts_matching_config() -> None:
    # Case-insensitive: configured names need not match the board's casing.
    github.validate_board_fields(_fields(), "sprint", "STATUS")


def test_validate_board_fields_rejects_missing_sprint_field() -> None:
    with pytest.raises(gh.PlateError) as excinfo:
        github.validate_board_fields(_fields(), "Cycle", "Status")
    message = str(excinfo.value)
    assert "sprintField" in message and '"Cycle"' in message
    assert "not a field" in message
    assert '"Sprint"' in message  # lists the board's real iteration field


def test_validate_board_fields_rejects_wrong_type_sprint_field() -> None:
    with pytest.raises(gh.PlateError) as excinfo:
        github.validate_board_fields(_fields(), "Status", "Status")
    message = str(excinfo.value)
    assert "sprintField" in message and '"Status"' in message
    assert "SINGLE_SELECT" in message  # names the actual (wrong) data type
    assert '"Sprint"' in message


def test_validate_board_fields_rejects_missing_status_field() -> None:
    with pytest.raises(gh.PlateError) as excinfo:
        github.validate_board_fields(_fields(), "Sprint", "Column")
    message = str(excinfo.value)
    assert "statusField" in message and '"Column"' in message
    assert '"Status"' in message  # lists the board's real single-select field


def test_validate_board_fields_rejects_wrong_type_status_field() -> None:
    with pytest.raises(gh.PlateError) as excinfo:
        github.validate_board_fields(_fields(), "Sprint", "Estimate")
    message = str(excinfo.value)
    assert "statusField" in message and "NUMBER" in message


def test_validate_board_fields_rejects_multiword_sprint_field() -> None:
    # #4: a multi-word iteration field can't be a filter qualifier — reject it
    # (path taken: reject, since the board filter only quotes values, not names).
    fields = [
        {"name": "Sprint Cycle", "dataType": "ITERATION"},
        {"name": "Status", "dataType": "SINGLE_SELECT"},
    ]
    with pytest.raises(gh.PlateError) as excinfo:
        github.validate_board_fields(fields, "Sprint Cycle", "Status")
    message = str(excinfo.value)
    assert "single-word" in message
    assert "sprint cycle:@current" in message  # shows the broken token


def test_single_select_options_unknown_field_returns_empty() -> None:
    # Field not on the board (or an options-less payload) -> no options listed.
    assert github._single_select_options(_fields(), "Column") == []
    assert github._single_select_options([], "Status") == []


# --- statusOrder validation (#7) ----------------------------------------------


def test_validate_board_fields_accepts_normalized_status_order() -> None:
    # Configured as displayed ("Priority", any case) against a board option
    # that carries an emoji ("🚀 Priority") — the same normalisation as
    # model.status_rank, so this must not raise.
    github.validate_board_fields(
        _fields_with_status_options(),
        "Sprint",
        "Status",
        ("Priority", "BACKLOG", "in progress"),
    )


def test_validate_board_fields_ignores_status_order_when_not_given() -> None:
    # No status_order passed -> defaults to () -> validation is skipped entirely,
    # matching every other existing call site of validate_board_fields.
    github.validate_board_fields(_fields_with_status_options(), "Sprint", "Status")


def test_validate_board_fields_rejects_unknown_status_order_entry() -> None:
    with pytest.raises(gh.PlateError) as excinfo:
        github.validate_board_fields(
            _fields_with_status_options(),
            "Sprint",
            "Status",
            ("Priority", "Blocked"),
        )
    message = str(excinfo.value)
    assert "statusOrder" in message and '"Blocked"' in message
    # the board's real options are listed, emoji-stripped (what the user sees)
    assert '"Priority"' in message
    assert '"In progress"' in message
    assert '"Backlog"' in message
    assert "🚀" not in message


# --- owner_search_query ------------------------------------------------------


@pytest.mark.parametrize(
    "owner_type,qualifier",
    [("organization", "org"), ("user", "user")],
)
def test_owner_search_query_uses_owner_type_qualifier(
    owner_type: str, qualifier: str
) -> None:
    query = github.owner_search_query("acme", owner_type)
    assert query.startswith(f"{qualifier}:acme ")
    assert "assignee:" not in query


@pytest.mark.parametrize("owner_type", ["organization", "user"])
def test_owner_search_query_always_excludes_archived_and_sorts(
    owner_type: str,
) -> None:
    query = github.owner_search_query("acme", owner_type)
    assert "archived:false" in query
    assert "sort:updated-desc" in query
    assert "is:issue" in query
    assert "is:open" in query


def test_owner_search_query_assignee_me_appends_filter() -> None:
    # --mine narrows with the @me token; no concrete login is needed to search.
    query = github.owner_search_query("acme", "organization", assignee="@me")
    assert query.endswith("assignee:@me")


def test_owner_search_query_explicit_assignee_appends_login() -> None:
    query = github.owner_search_query("acme", "organization", assignee="alice")
    assert query.endswith("assignee:alice")


def test_owner_search_query_no_assignee_omits_filter() -> None:
    query = github.owner_search_query("acme", "organization", assignee=None)
    assert "assignee" not in query


# --- resolve_owner_type -------------------------------------------------------


def _fake_run_with_stdout(stdout: str, returncode: int = 0) -> Any:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, returncode, stdout=stdout, stderr="" if returncode == 0 else stdout
        )

    return fake_run


def test_resolve_owner_type_maps_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="Organization\n", stderr="")

    monkeypatch.setattr(gh, "run_command", fake_run)
    assert gh.resolve_owner_type("acme") == "organization"
    assert captured["args"] == ["gh", "api", "users/acme", "--jq", ".type"]


def test_resolve_owner_type_maps_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout("User\n"))
    assert gh.resolve_owner_type("octocat") == "user"


def test_resolve_owner_type_gh_failure_raises_with_owner_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="HTTP 404: Not Found"
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(gh.PlateError, match="no-such-owner"):
        gh.resolve_owner_type("no-such-owner")


def test_resolve_owner_type_unexpected_type_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout("Bot\n"))
    with pytest.raises(gh.PlateError, match="acme-bot"):
        gh.resolve_owner_type("acme-bot")


# --- repo_from_remote fallback / current_repo / current_login ----------------


def test_repo_from_remote_non_github_host_falls_back_to_gh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="an-org/a-repo\n", stderr="")

    monkeypatch.setattr(gh, "run_command", fake_run)
    repo = gh.repo_from_remote("git@git.example.com:an-org/a-repo.git")
    assert repo == "an-org/a-repo"
    assert captured["args"] == [
        "gh",
        "repo",
        "view",
        "--json",
        "nameWithOwner",
        "--jq",
        ".nameWithOwner",
    ]


def test_repo_from_remote_fallback_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout("boom", returncode=1))
    assert gh.repo_from_remote("git@git.example.com:x/y.git") is None


def test_repo_from_remote_fallback_empty_stdout_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout("\n"))
    assert gh.repo_from_remote("git@git.example.com:x/y.git") is None


def test_current_repo_parses_origin_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gh, "run_command", _fake_run_with_stdout("git@github.com:an-org/a-repo.git\n")
    )
    assert gh.current_repo() == "an-org/a-repo"


def test_current_repo_outside_git_repo_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gh,
        "run_command",
        _fake_run_with_stdout("fatal: not a git repository", returncode=1),
    )
    with pytest.raises(gh.PlateError) as excinfo:
        gh.current_repo()
    message = str(excinfo.value)
    assert "Not inside a git repository" in message
    assert "--repo OWNER/REPO" in message


def test_current_repo_unresolvable_remote_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # git yields a non-github remote; the gh fallback then fails too.
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[0] == "git":
            return subprocess.CompletedProcess(
                args, 0, stdout="git@git.example.com:x/y.git\n", stderr=""
            )
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(gh.PlateError) as excinfo:
        gh.current_repo()
    message = str(excinfo.value)
    assert "Could not derive OWNER/REPO" in message
    assert "git@git.example.com:x/y.git" in message
    assert "--repo OWNER/REPO" in message


def test_current_login_returns_stripped_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout("hub-user\n"))
    assert gh.current_login() == "hub-user"


def test_current_login_gh_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout("", returncode=1))
    assert gh.current_login() is None


def test_current_login_empty_stdout_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout("\n"))
    assert gh.current_login() is None


# --- _search_issues / fetch_assigned_issues / fetch_owner_issues -------------


def _search_payload(
    issue_count: int,
    nodes: list[dict[str, Any]],
    *,
    has_next: bool = False,
    end_cursor: str | None = None,
    viewer: str | None = "hub-user",
) -> str:
    """A gh GraphQL search response page. ``viewer`` mimics the real query's
    ``viewer { login }`` root (present on every page); ``None`` omits it."""
    data: dict[str, Any] = {
        "search": {
            "issueCount": issue_count,
            "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
            "nodes": nodes,
        }
    }
    if viewer is not None:
        data["viewer"] = {"login": viewer}
    return json.dumps({"data": data})


def _paged_fake_run(pages: list[str]) -> Any:
    """A fake ``run_command`` that returns ``pages`` in order, one gh call each."""
    calls = {"n": 0}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        stdout = pages[calls["n"]]
        calls["n"] += 1
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    fake_run.calls = calls  # type: ignore[attr-defined]
    return fake_run


def test_fetch_assigned_issues_single_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gh,
        "run_command",
        _paged_fake_run([_search_payload(2, [{"number": 1}, {"number": 2}])]),
    )
    issues, total, viewer = github.fetch_assigned_issues("an-org/a-repo", 10)
    assert [i["number"] for i in issues] == [1, 2]
    assert total == 2
    assert viewer == "hub-user"


def test_fetch_assigned_issues_searches_assignee_me(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The default search needs no concrete login: @me filters server-side while
    # viewer { login } returns the real login in the same round trip.
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(
            args, 0, stdout=_search_payload(0, []), stderr=""
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    github.fetch_assigned_issues("an-org/a-repo", 10)

    q_arg = next(a for a in captured["args"] if a.startswith("q="))
    assert q_arg == "q=repo:an-org/a-repo is:issue is:open assignee:@me"


def test_fetch_assigned_issues_explicit_assignee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(
            args, 0, stdout=_search_payload(0, []), stderr=""
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    github.fetch_assigned_issues("an-org/a-repo", 10, assignee="alice")

    q_arg = next(a for a in captured["args"] if a.startswith("q="))
    assert q_arg.endswith("assignee:alice")


def test_fetch_assigned_issues_gh_failure_raises_with_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(gh.PlateError, match="an-org/a-repo"):
        github.fetch_assigned_issues("an-org/a-repo", 10)


def test_fetch_owner_issues_issues_the_expected_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(
            args, 0, stdout=_search_payload(0, []), stderr=""
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    github.fetch_owner_issues("acme", "organization", 10, assignee="@me")

    q_arg = next(a for a in captured["args"] if a.startswith("q="))
    expected = github.owner_search_query("acme", "organization", assignee="@me")
    assert q_arg == f"q={expected}"


def test_fetch_owner_issues_paginates_across_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        _search_payload(
            3, [{"number": 1}, {"number": 2}], has_next=True, end_cursor="CURSOR1"
        ),
        _search_payload(3, [{"number": 3}]),
    ]
    fake_run = _paged_fake_run(pages)
    monkeypatch.setattr(gh, "run_command", fake_run)

    issues, total, viewer = github.fetch_owner_issues("acme", "organization", 10)
    assert [i["number"] for i in issues] == [1, 2, 3]
    assert total == 3
    assert viewer == "hub-user"
    assert fake_run.calls["n"] == 2  # type: ignore[attr-defined]


def test_fetch_owner_issues_viewer_from_whichever_page_has_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The viewer field rides on every page in reality; the merge must not
    # depend on any particular page carrying it.
    pages = [
        _search_payload(
            2, [{"number": 1}], has_next=True, end_cursor="CURSOR1", viewer=None
        ),
        _search_payload(2, [{"number": 2}]),
    ]
    monkeypatch.setattr(gh, "run_command", _paged_fake_run(pages))

    _issues, _total, viewer = github.fetch_owner_issues("acme", "organization", 10)
    assert viewer == "hub-user"


def test_fetch_owner_issues_truncates_at_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=_search_payload(
                100,
                [{"number": i} for i in range(1, 11)],
                has_next=True,
                end_cursor="CURSOR",
            ),
            stderr="",
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    issues, total, _viewer = github.fetch_owner_issues("acme", "organization", 5)
    assert len(issues) == 5
    assert total == 100  # server total can exceed what limit lets through


def test_fetch_owner_issues_gh_failure_raises_with_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(gh.PlateError, match="acme"):
        github.fetch_owner_issues("acme", "organization", 5)


# --- search_paginated transient-5xx handling (GitHub search timeouts) -------
#
# These drive gh.search_paginated directly with a stub query document; the
# sleep between retry attempts is patched out so tests are instant.


def _page_size_of(args: list[str]) -> int:
    arg = next(a for a in args if a.startswith("pageSize="))
    return int(arg.removeprefix("pageSize="))


def _flaky_fake_run(failures: int, stdout: str) -> Any:
    """A fake ``run_command`` failing with HTTP 502 ``failures`` times first."""
    seen: list[list[str]] = []

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        if len(seen) <= failures:
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="gh: HTTP 502"
            )
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    fake_run.seen = seen  # type: ignore[attr-defined]
    return fake_run


def test_search_paginated_retries_transient_502_with_smaller_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = _flaky_fake_run(1, _search_payload(1, [{"number": 7}]))
    monkeypatch.setattr(gh, "run_command", fake_run)
    monkeypatch.setattr(gh.time, "sleep", lambda seconds: None)

    nodes, total = gh.search_paginated("QUERY", "q-str", 500, "acme")

    assert [n["number"] for n in nodes] == [7]
    assert total == 1
    seen = fake_run.seen  # type: ignore[attr-defined]
    assert [_page_size_of(args) for args in seen] == [100, 50]


def test_search_paginated_persistent_502_raises_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = _flaky_fake_run(99, "")
    monkeypatch.setattr(gh, "run_command", fake_run)
    monkeypatch.setattr(gh.time, "sleep", lambda seconds: None)

    with pytest.raises(gh.PlateError) as excinfo:
        gh.search_paginated("QUERY", "q-str", 500, "acme")

    message = str(excinfo.value)
    assert "acme" in message
    assert "HTTP 502" in message
    assert "server-side" in message
    assert len(fake_run.seen) == gh.MAX_ATTEMPTS  # type: ignore[attr-defined]


def test_search_paginated_non_transient_failure_does_not_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="gh: HTTP 401 Bad credentials"
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(gh.PlateError, match="Bad credentials"):
        gh.search_paginated("QUERY", "q-str", 500, "acme")


def test_search_paginated_rate_limit_failure_explains_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="gh: API rate limit exceeded for user ID 1."
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(gh.PlateError) as excinfo:
        gh.search_paginated("QUERY", "q-str", 500, "acme")

    message = str(excinfo.value)
    assert "API rate limit exceeded" in message  # the raw stderr is kept
    assert "GitHub is rate limiting this token" in message
    assert "narrow the query (--repo, --mine, a smaller --limit)" in message


def test_search_paginated_unrelated_failure_gets_no_rate_limit_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="gh: HTTP 422 Validation failed"
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    with pytest.raises(gh.PlateError) as excinfo:
        gh.search_paginated("QUERY", "q-str", 500, "acme")

    assert "rate limiting" not in str(excinfo.value)


def test_search_paginated_invalid_json_raises_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout("not json"))
    with pytest.raises(gh.PlateError, match="Could not parse gh response"):
        gh.search_paginated("QUERY", "q-str", 10, "acme")


def test_search_paginated_graphql_errors_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # gh exits 0 yet the payload carries a top-level errors list.
    payload = json.dumps({"errors": [{"message": "Something went wrong"}]})
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout(payload))
    with pytest.raises(gh.PlateError) as excinfo:
        gh.search_paginated("QUERY", "q-str", 10, "acme")
    message = str(excinfo.value)
    assert "GraphQL error" in message
    assert "Something went wrong" in message


def test_search_paginated_invalid_json_on_second_page_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A good first page, then garbage mid-pagination.
    pages = [
        _search_payload(2, [{"number": 1}], has_next=True, end_cursor="CURSOR1"),
        "not json",
    ]
    monkeypatch.setattr(gh, "run_command", _paged_fake_run(pages))
    with pytest.raises(gh.PlateError, match="Could not parse gh response"):
        gh.search_paginated("QUERY", "q-str", 10, "acme")


def test_search_paginated_with_viewer_returns_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = _flaky_fake_run(0, _search_payload(1, [{"number": 7}]))
    monkeypatch.setattr(gh, "run_command", fake_run)

    nodes, total, viewer = gh.search_paginated_with_viewer(
        "QUERY", "q-str", 500, "acme"
    )

    assert [n["number"] for n in nodes] == [7]
    assert total == 1
    assert viewer == "hub-user"


def test_search_paginated_with_viewer_none_when_document_lacks_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = _flaky_fake_run(0, _search_payload(1, [{"number": 7}], viewer=None))
    monkeypatch.setattr(gh, "run_command", fake_run)

    _nodes, _total, viewer = gh.search_paginated_with_viewer(
        "QUERY", "q-str", 500, "acme"
    )
    assert viewer is None


def test_search_paginated_keeps_two_tuple_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The prs owner view still consumes the viewer-less wrapper unchanged.
    fake_run = _flaky_fake_run(0, _search_payload(1, [{"number": 7}]))
    monkeypatch.setattr(gh, "run_command", fake_run)

    result = gh.search_paginated("QUERY", "q-str", 500, "acme")
    assert result == ([{"number": 7}], 1)


def test_search_paginated_requests_only_what_limit_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = _flaky_fake_run(0, _search_payload(2, [{"number": 1}]))
    monkeypatch.setattr(gh, "run_command", fake_run)

    gh.search_paginated("QUERY", "q-str", 5, "acme")

    seen = fake_run.seen  # type: ignore[attr-defined]
    assert _page_size_of(seen[0]) == 5


# --- the transient stderr progress line -------------------------------------


class _TtyStderr(io.StringIO):
    """A StringIO posing as a terminal, so ``gh.progress`` writes to it."""

    def isatty(self) -> bool:
        return True


def test_search_paginated_paints_and_clears_progress_on_a_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = _TtyStderr()
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(
        gh, "run_command", _flaky_fake_run(0, _search_payload(1, [{"number": 1}]))
    )

    gh.search_paginated("QUERY", "q-str", 500, "acme")

    output = stderr.getvalue()
    assert "Fetching from GitHub for acme…" in output
    assert output.endswith("\r\x1b[2K")  # cleared before real output renders


def test_search_paginated_progress_reports_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = _TtyStderr()
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(gh.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        gh, "run_command", _flaky_fake_run(1, _search_payload(1, [{"number": 1}]))
    )

    gh.search_paginated("QUERY", "q-str", 500, "acme")

    output = stderr.getvalue()
    assert "GitHub timed out (HTTP 502)" in output
    assert "page size 50" in output
    assert output.endswith("\r\x1b[2K")


def test_search_paginated_clears_progress_when_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = _TtyStderr()
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(gh.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(gh, "run_command", _flaky_fake_run(99, ""))

    with pytest.raises(gh.PlateError):
        gh.search_paginated("QUERY", "q-str", 500, "acme")

    assert stderr.getvalue().endswith("\r\x1b[2K")


def test_search_paginated_is_silent_when_stderr_is_not_a_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = io.StringIO()  # isatty() is False
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(
        gh, "run_command", _flaky_fake_run(0, _search_payload(1, [{"number": 1}]))
    )

    gh.search_paginated("QUERY", "q-str", 500, "acme")

    assert stderr.getvalue() == ""


# --- fetch_sprint_items: items + viewer login --------------------------------


def _sprint_payload(
    nodes: list[dict[str, Any]],
    *,
    viewer: str | None = "hub-user",
    has_next: bool = False,
    end_cursor: str | None = None,
) -> str:
    data: dict[str, Any] = {
        "organization": {
            "projectV2": {
                "items": {
                    "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                    "nodes": nodes,
                }
            }
        }
    }
    if viewer is not None:
        data["viewer"] = {"login": viewer}
    return json.dumps({"data": data})


def test_fetch_sprint_items_returns_items_and_viewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _sprint_payload([{"content": {"__typename": "Issue", "number": 1}}])
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout(payload))

    items, viewer = github.fetch_sprint_items(
        "acme", "organization", 2, "Iteration", "Status"
    )
    assert len(items) == 1
    assert viewer == "hub-user"


def test_fetch_sprint_items_viewer_none_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _sprint_payload([], viewer=None)
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout(payload))

    items, viewer = github.fetch_sprint_items(
        "acme", "organization", 2, "Iteration", "Status"
    )
    assert items == []
    assert viewer is None


def test_fetch_sprint_items_paginates_with_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        _sprint_payload([{"n": 1}], has_next=True, end_cursor="CURSOR1"),
        _sprint_payload([{"n": 2}]),
    ]
    seen: list[list[str]] = []

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return subprocess.CompletedProcess(
            args, 0, stdout=pages[len(seen) - 1], stderr=""
        )

    monkeypatch.setattr(gh, "run_command", fake_run)
    items, viewer = github.fetch_sprint_items(
        "acme", "organization", 2, "Iteration", "Status"
    )
    assert [i["n"] for i in items] == [1, 2]
    assert viewer == "hub-user"
    assert len(seen) == 2
    assert "endCursor=CURSOR1" in seen[1]
    assert not any(a.startswith("endCursor=") for a in seen[0])


def test_fetch_sprint_items_caps_runaway_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Backstop for a broken board filter matching everything: one page already
    # over the cap must truncate and stop, not follow the cursor.
    nodes = [{"n": i} for i in range(github.SPRINT_ITEM_CAP + 1)]
    fake_run = _paged_fake_run(
        [_sprint_payload(nodes, has_next=True, end_cursor="CURSOR")]
    )
    monkeypatch.setattr(gh, "run_command", fake_run)

    items, _viewer = github.fetch_sprint_items(
        "acme", "organization", 2, "Iteration", "Status"
    )
    assert len(items) == github.SPRINT_ITEM_CAP
    assert fake_run.calls["n"] == 1  # type: ignore[attr-defined]


def test_fetch_sprint_items_gh_failure_raises_with_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout("boom", returncode=1))
    with pytest.raises(gh.PlateError) as excinfo:
        github.fetch_sprint_items("acme", "organization", 2, "Iteration", "Status")
    message = str(excinfo.value)
    assert "gh failed to fetch project acme/2" in message
    assert "boom" in message


def test_fetch_sprint_items_invalid_json_raises_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout("not json"))
    with pytest.raises(gh.PlateError, match="Could not parse gh response"):
        github.fetch_sprint_items("acme", "organization", 2, "Iteration", "Status")


def test_fetch_sprint_items_missing_project_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps({"data": {"organization": {"projectV2": None}}})
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout(payload))
    with pytest.raises(gh.PlateError) as excinfo:
        github.fetch_sprint_items("acme", "organization", 2, "Iteration", "Status")
    message = str(excinfo.value)
    assert "No project #2 found for acme" in message
    assert "read:project" in message


# --- fetch_sprint_items / fetch_project_fields: GraphQL error handling ------


def test_fetch_sprint_items_insufficient_scopes_raises_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps(
        {"errors": [{"type": "INSUFFICIENT_SCOPES", "message": "missing scope"}]}
    )
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout(payload))

    with pytest.raises(gh.PlateError, match="read:project"):
        github.fetch_sprint_items("acme", "organization", 2, "Iteration", "Status")

    with pytest.raises(gh.PlateError, match="gh auth refresh -s read:project"):
        github.fetch_sprint_items("acme", "organization", 2, "Iteration", "Status")


def test_fetch_sprint_items_other_graphql_error_raises_generic_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps({"errors": [{"type": "NOT_FOUND", "message": "nope"}]})
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout(payload))

    with pytest.raises(gh.PlateError, match="GraphQL error"):
        github.fetch_sprint_items("acme", "organization", 2, "Iteration", "Status")


def test_fetch_project_fields_insufficient_scopes_raises_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps(
        {"errors": [{"type": "INSUFFICIENT_SCOPES", "message": "missing scope"}]}
    )
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout(payload))

    with pytest.raises(gh.PlateError, match="gh auth refresh -s read:project"):
        github.fetch_project_fields("acme", "organization", 2)


def test_fetch_project_fields_other_graphql_error_raises_generic_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps({"errors": [{"type": "NOT_FOUND", "message": "nope"}]})
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout(payload))

    with pytest.raises(gh.PlateError, match="GraphQL error"):
        github.fetch_project_fields("acme", "organization", 2)


def test_fetch_project_fields_gh_failure_raises_with_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout("boom", returncode=1))
    with pytest.raises(gh.PlateError) as excinfo:
        github.fetch_project_fields("acme", "organization", 2)
    message = str(excinfo.value)
    assert "gh failed to fetch fields for project acme/2" in message
    assert "boom" in message


def test_fetch_project_fields_invalid_json_raises_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout("not json"))
    with pytest.raises(gh.PlateError, match="Could not parse gh response"):
        github.fetch_project_fields("acme", "organization", 2)


def test_fetch_project_fields_missing_project_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps({"data": {"organization": {"projectV2": None}}})
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout(payload))
    with pytest.raises(gh.PlateError) as excinfo:
        github.fetch_project_fields("acme", "organization", 2)
    message = str(excinfo.value)
    assert "No project #2 found for acme" in message
    assert "read:project" in message


def test_fetch_project_fields_returns_dict_nodes_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A null node (deleted field mid-query) must be dropped, not returned.
    payload = json.dumps(
        {
            "data": {
                "organization": {
                    "projectV2": {
                        "fields": {
                            "nodes": [
                                {"name": "Sprint", "dataType": "ITERATION"},
                                None,
                                {
                                    "name": "Status",
                                    "dataType": "SINGLE_SELECT",
                                    "options": [{"name": "Backlog"}],
                                },
                            ]
                        }
                    }
                }
            }
        }
    )
    monkeypatch.setattr(gh, "run_command", _fake_run_with_stdout(payload))
    fields = github.fetch_project_fields("acme", "organization", 2)
    assert [f["name"] for f in fields] == ["Sprint", "Status"]
    assert fields[1]["options"] == [{"name": "Backlog"}]


def test_format_names_cleans_board_text() -> None:
    assert github._format_names(["Todo\x1b[2J\x1b[H", "Done"]) == '"Todo", "Done"'
    assert github._format_names([]) == "none"
