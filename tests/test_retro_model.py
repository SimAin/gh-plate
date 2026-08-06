"""Tests for plate.retro.model — push chaining, compare expansion, owner
attribution, and day bucketing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from plate.retro import model

NOW = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)  # a Friday


def _iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def commit_ref(days_ago: int, owner: str = "acme") -> tuple[str, str]:
    return (owner, _iso(days_ago))


def opened(days_ago: int, repo: str = "acme/widget") -> dict[str, Any]:
    return {
        "repository_url": f"https://api.github.com/repos/{repo}",
        "created_at": _iso(days_ago),
    }


def review(days_ago: int, repo: str = "acme/widget") -> dict[str, Any]:
    return {
        "type": "PullRequestReviewEvent",
        "repo": {"name": repo},
        "created_at": _iso(days_ago),
    }


def push(
    days_ago: int,
    repo: str = "acme/widget",
    ref: str = "refs/heads/feat/x",
    base: str = "a" * 8,
    head: str = "b" * 8,
) -> dict[str, Any]:
    return {
        "type": "PushEvent",
        "repo": {"name": repo},
        "created_at": _iso(days_ago),
        "payload": {"ref": ref, "before": base, "head": head},
    }


def compare_commit(
    days_ago: int, login: str = "simon", sha: str = ""
) -> dict[str, Any]:
    return {
        "sha": sha or f"sha-{days_ago}-{login}",
        "author": {"login": login},
        "commit": {"committer": {"date": _iso(days_ago)}},
    }


def sections(
    commits: list[tuple[str, str]] | None = None,
    prs: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    days: int = 14,
) -> list[model.RetroSection]:
    return model.build_sections(commits or [], prs or [], events or [], days, NOW)


def one_owner(
    commits: list[tuple[str, str]] | None = None,
    prs: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    days: int = 14,
) -> dict[str, model.RetroChannel]:
    built = sections(commits, prs, events, days)
    assert len(built) == 1
    return {channel.label: channel for channel in built[0].channels}


# --- push_groups -----------------------------------------------------------------


def test_push_groups_chain_per_branch_oldest_base_to_newest_head() -> None:
    events = [  # feed order: newest first
        push(0, base="c3", head="c4"),
        push(2, base="c1", head="c2"),
        push(1, repo="acme/other", base="x1", head="x2"),
    ]
    groups = {(g.repo, g.base, g.head): g for g in model.push_groups(events, 14, NOW)}
    assert ("acme/widget", "c1", "c4") in groups
    assert ("acme/other", "x1", "x2") in groups
    chained = groups[("acme/widget", "c1", "c4")]
    assert len(chained.push_stamps) == 2


def test_push_groups_split_by_branch_within_a_repo() -> None:
    events = [
        push(0, ref="refs/heads/feat/a"),
        push(0, ref="refs/heads/feat/b"),
    ]
    assert len(model.push_groups(events, 14, NOW)) == 2


def test_push_groups_drop_out_of_window_and_malformed_pushes() -> None:
    events = [
        push(14),  # outside a 14-day window
        {"type": "PushEvent", "repo": {"name": "acme/widget"}, "payload": {}},
        {"type": "PushEvent", "created_at": _iso(1), "payload": None},
        review(1),
    ]
    assert model.push_groups(events, 14, NOW) == []


# --- commits_from_compares --------------------------------------------------------


def _group(days_ago: list[int], repo: str = "acme/widget") -> model.PushGroup:
    return model.PushGroup(
        repo=repo,
        base="a" * 8,
        head="b" * 8,
        push_stamps=[_iso(d) for d in days_ago],
    )


def test_compare_keeps_only_your_commits_with_their_dates() -> None:
    compare = {
        "commits": [
            compare_commit(1),
            compare_commit(2, login="alice"),
            {"sha": "s", "author": None, "commit": {}},
        ]
    }
    refs, unexpanded = model.commits_from_compares([_group([0])], [compare], "simon")
    assert refs == [("acme", _iso(1))]
    assert unexpanded == 0


def test_compare_dedupes_shas_across_branches() -> None:
    shared = compare_commit(1, sha="same-sha")
    refs, _ = model.commits_from_compares(
        [_group([0]), _group([0], repo="acme/widget")],
        [{"commits": [shared]}, {"commits": [shared]}],
        "simon",
    )
    assert len(refs) == 1


def test_failed_compare_falls_back_to_one_commit_per_push() -> None:
    refs, unexpanded = model.commits_from_compares(
        [_group([0, 3])], [None], "simon"
    )
    assert refs == [("acme", _iso(0)), ("acme", _iso(3))]
    assert unexpanded == 2
    note = model.unexpanded_note(unexpanded)
    assert note is not None and "2 pushes" in note
    assert model.unexpanded_note(0) is None


# --- build_sections ----------------------------------------------------------------


def test_channels_come_in_display_order() -> None:
    section = sections(commits=[commit_ref(1)])[0]
    assert [c.label for c in section.channels] == ["reviews", "commits", "opened"]


def test_activity_splits_by_repository_owner() -> None:
    built = sections(
        commits=[commit_ref(1, "acme"), commit_ref(2, "SimAin")],
        prs=[opened(1, "acme/widget")],
        events=[review(0, "acme/gadget")],
    )
    assert [section.owner for section in built] == ["acme", "SimAin"]
    acme = {c.label: c for c in built[0].channels}
    personal = {c.label: c for c in built[1].channels}
    assert acme["commits"].total == 1
    assert acme["opened"].total == 1
    assert acme["reviews"].total == 1
    assert personal["commits"].total == 1
    assert personal["reviews"].total == 0


def test_sections_order_most_active_first_then_name() -> None:
    built = sections(
        commits=[commit_ref(1, "small")] + [commit_ref(1, "busy")] * 3,
        events=[review(1, "also-small/z")],
    )
    assert [section.owner for section in built] == ["busy", "also-small", "small"]


def test_events_bucket_by_day_oldest_first() -> None:
    reviews = one_owner(events=[review(0), review(2), review(2)])["reviews"]
    assert len(reviews.counts) == 14
    assert reviews.counts[-1] == 1  # today is the last entry
    assert reviews.counts[-3] == 2
    assert reviews.total == 3


def test_only_review_events_count() -> None:
    events: list[dict[str, Any]] = [
        review(1),
        push(1),
        {"type": "IssuesEvent", "repo": {"name": "acme/widget"}, "created_at": _iso(1)},
    ]
    assert one_owner(events=events)["reviews"].total == 1


def test_activity_outside_the_window_is_dropped() -> None:
    built = sections(commits=[commit_ref(14)], days=14)
    assert built == []  # a fully quiet owner is dropped
    oldest = one_owner(commits=[commit_ref(13)], days=14)["commits"]
    assert oldest.counts[0] == 1


def test_last_days_edges() -> None:
    labelled = one_owner(
        events=[review(0)], commits=[commit_ref(5), commit_ref(9)]
    )
    assert labelled["reviews"].last_days == 0
    assert labelled["commits"].last_days == 5
    assert labelled["opened"].last_days is None


def test_totals_equal_the_sum_of_the_cells_and_the_section_total() -> None:
    built = sections(
        commits=[commit_ref(1), commit_ref(6)],
        prs=[opened(2)],
        events=[review(0), review(1)],
    )
    section = built[0]
    for channel in section.channels:
        assert channel.total == sum(channel.counts)
    assert section.total == sum(channel.total for channel in section.channels)


def test_malformed_items_are_ignored() -> None:
    commits: list[tuple[str, Any]] = [commit_ref(1), ("acme", None), ("acme", "junk")]
    prs: list[dict[str, Any]] = [{"repository_url": "junk", "created_at": _iso(1)}]
    labelled = one_owner(commits=commits, prs=prs)
    assert labelled["commits"].total == 1
    assert labelled["opened"].total == 0


def test_no_activity_yields_no_sections() -> None:
    assert sections() == []


def test_window_start_is_midnight_utc_of_the_oldest_day() -> None:
    start = model.window_start(14, NOW)
    assert start == datetime(2026, 6, 6, 0, 0, tzinfo=UTC)
    assert model.window_start(7, NOW).date() == NOW.date() - timedelta(days=6)


# --- honesty notes ---------------------------------------------------------------


def test_short_feed_always_covers_the_window() -> None:
    assert model.coverage_note([review(1)], 14, NOW, feed_cap=300) is None


def test_capped_feed_reaching_past_the_window_is_covered() -> None:
    events = [review(20)] * 300  # oldest event predates a 14-day window
    assert model.coverage_note(events, 14, NOW, feed_cap=300) is None


def test_capped_feed_short_of_the_window_warns() -> None:
    events = [review(3)] * 300  # 300 events, none older than 3 days
    note = model.coverage_note(events, 14, NOW, feed_cap=300)
    assert note is not None
    assert "review and commit counts" in note


def test_truncation_note_only_when_search_clipped() -> None:
    note = model.truncation_note("PRs opened", 1000, 1500)
    assert note is not None and "1000 of 1500 PRs opened" in note
    assert model.truncation_note("PRs opened", 30, 30) is None
