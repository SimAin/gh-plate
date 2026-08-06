"""Domain model: raw activity events -> per-channel day buckets.

Pure functions only — no subprocess, no I/O, no printing, no rendering. Three
channels — reviews, pushes, PRs opened — each bucketed into one count per UTC
day over the window. Pushes, not commits: a private-repo PushEvent carries no
commit count, so the honest figure is pushes per day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

DEFAULT_DAYS = 14
MIN_DAYS = 7
MAX_DAYS = 30


@dataclass(frozen=True)
class RetroChannel:
    """One activity channel: day counts (oldest first, last entry = today),
    their total, and days since the channel's most recent activity (None =
    quiet for the whole window)."""

    label: str
    counts: list[int]
    total: int
    last_days: int | None


def window_start(days: int, now: datetime) -> datetime:
    """The start of the window's oldest UTC day — today counts as day one."""
    oldest = now.astimezone(UTC).date() - timedelta(days=days - 1)
    return datetime.combine(oldest, time.min, tzinfo=UTC)


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def event_channel(event: dict[str, Any]) -> str | None:
    """Which channel an event belongs to, or None when it counts for nothing.

    A PullRequestEvent counts only when its action is "opened" — the feed
    also reports merges, label churn, and assignments under the same type.
    """
    event_type = event.get("type")
    if event_type == "PushEvent":
        return "pushes"
    if event_type == "PullRequestReviewEvent":
        return "reviews"
    if event_type == "PullRequestEvent":
        payload = event.get("payload")
        action = payload.get("action") if isinstance(payload, dict) else None
        return "opened" if action == "opened" else None
    return None


def _channel(label: str, days_ago: list[int], days: int) -> RetroChannel:
    counts = [0] * days
    for age in days_ago:
        if 0 <= age < days:
            counts[days - 1 - age] += 1
    last_days = next(
        (days - 1 - i for i in range(days - 1, -1, -1) if counts[i]), None
    )
    return RetroChannel(
        label=label, counts=counts, total=sum(counts), last_days=last_days
    )


def build_channels(
    events: list[dict[str, Any]], days: int, now: datetime
) -> list[RetroChannel]:
    """The three channels, in display order: reviews, pushes, opened."""
    today = now.astimezone(UTC).date()
    ages: dict[str, list[int]] = {"reviews": [], "pushes": [], "opened": []}
    for event in events:
        channel = event_channel(event)
        if channel is None:
            continue
        timestamp = parse_timestamp(event.get("created_at"))
        if timestamp is None:
            continue
        ages[channel].append((today - timestamp.astimezone(UTC).date()).days)
    return [_channel(label, ages[label], days) for label in ages]


def feed_covers_window(
    events: list[dict[str, Any]], days: int, now: datetime, feed_cap: int
) -> bool:
    """Whether the feed reaches back to the window's start.

    A feed shorter than the cap is everything GitHub retains, so it covers
    any window; a capped feed covers the window only if its oldest event
    predates the window's oldest day.
    """
    if len(events) < feed_cap:
        return True
    timestamps = [
        parsed
        for event in events
        if (parsed := parse_timestamp(event.get("created_at"))) is not None
    ]
    if not timestamps:
        return True
    return min(timestamps) <= window_start(days, now)


def coverage_note(
    events: list[dict[str, Any]], days: int, now: datetime, feed_cap: int
) -> str | None:
    """The undercount warning, or None when the window is fully covered."""
    if feed_covers_window(events, days, now, feed_cap):
        return None
    return (
        f"Note: GitHub keeps only your {feed_cap} most recent events; "
        "early days of this window may be undercounted."
    )
