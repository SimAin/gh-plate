"""Tests for plate.retro.model — the pure event-bucketing layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from plate.retro import model

NOW = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)  # a Friday


def _iso(days_ago: int, hours_ago: int = 0) -> str:
    stamp = NOW - timedelta(days=days_ago, hours=hours_ago)
    return stamp.isoformat().replace("+00:00", "Z")


def push(days_ago: int) -> dict[str, Any]:
    return {"type": "PushEvent", "created_at": _iso(days_ago), "payload": {}}


def review(days_ago: int) -> dict[str, Any]:
    return {
        "type": "PullRequestReviewEvent",
        "created_at": _iso(days_ago),
        "payload": {"action": "created"},
    }


def pr_event(days_ago: int, action: str = "opened") -> dict[str, Any]:
    return {
        "type": "PullRequestEvent",
        "created_at": _iso(days_ago),
        "payload": {"action": action},
    }


def by_label(
    events: list[dict[str, Any]], days: int = 14
) -> dict[str, model.RetroChannel]:
    channels = model.build_channels(events, days, NOW)
    return {channel.label: channel for channel in channels}


def test_channels_come_in_display_order() -> None:
    labels = [c.label for c in model.build_channels([], 14, NOW)]
    assert labels == ["reviews", "pushes", "opened"]


def test_events_bucket_by_day_oldest_first() -> None:
    reviews = by_label([review(0), review(2), review(2)])["reviews"]
    assert len(reviews.counts) == 14
    assert reviews.counts[-1] == 1  # today is the last entry
    assert reviews.counts[-3] == 2
    assert reviews.total == 3


def test_only_opened_pr_actions_count() -> None:
    events = [
        pr_event(1, "opened"),
        pr_event(1, "merged"),
        pr_event(1, "labeled"),
        pr_event(1, "assigned"),
    ]
    assert by_label(events)["opened"].total == 1


def test_unrelated_event_types_count_for_nothing() -> None:
    events: list[dict[str, Any]] = [
        {"type": "IssuesEvent", "created_at": _iso(1), "payload": {}},
        {"type": "IssueCommentEvent", "created_at": _iso(1), "payload": {}},
        {"type": "CreateEvent", "created_at": _iso(1), "payload": {}},
    ]
    assert all(channel.total == 0 for channel in by_label(events).values())


def test_events_outside_the_window_are_dropped() -> None:
    assert by_label([push(14)], days=14)["pushes"].total == 0
    oldest = by_label([push(13)], days=14)["pushes"]
    assert oldest.counts[0] == 1


def test_last_days_edges() -> None:
    labelled = by_label([review(0), push(5), push(9)])
    assert labelled["reviews"].last_days == 0
    assert labelled["pushes"].last_days == 5
    assert labelled["opened"].last_days is None


def test_total_always_equals_the_sum_of_the_cells() -> None:
    events = [review(0), review(1), push(1), push(6), pr_event(2)]
    for channel in model.build_channels(events, 14, NOW):
        assert channel.total == sum(channel.counts)


def test_malformed_events_are_ignored() -> None:
    events: list[dict[str, Any]] = [
        push(1),
        {"type": "PushEvent", "created_at": None, "payload": {}},
        {"type": "PushEvent", "created_at": "not a timestamp", "payload": {}},
        {"type": "PullRequestEvent", "created_at": _iso(1), "payload": None},
    ]
    assert by_label(events)["pushes"].total == 1


def test_empty_feed_yields_quiet_channels() -> None:
    for channel in model.build_channels([], 14, NOW):
        assert channel.total == 0
        assert channel.last_days is None
        assert len(channel.counts) == 14


def test_window_start_is_midnight_utc_of_the_oldest_day() -> None:
    start = model.window_start(14, NOW)
    assert start == datetime(2026, 6, 6, 0, 0, tzinfo=UTC)
    assert model.window_start(7, NOW).date() == NOW.date() - timedelta(days=6)


# --- feed coverage ---------------------------------------------------------------


def test_short_feed_always_covers_the_window() -> None:
    assert model.coverage_note([push(1)], 14, NOW, feed_cap=300) is None


def test_capped_feed_reaching_past_the_window_is_covered() -> None:
    events = [push(20)] * 300  # oldest event predates a 14-day window
    assert model.coverage_note(events, 14, NOW, feed_cap=300) is None


def test_capped_feed_short_of_the_window_warns() -> None:
    events = [push(3)] * 300  # 300 events, none older than 3 days
    note = model.coverage_note(events, 14, NOW, feed_cap=300)
    assert note is not None
    assert "300 most recent events" in note
