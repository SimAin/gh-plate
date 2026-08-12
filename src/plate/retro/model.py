"""Domain model: raw events, search items, and compare payloads -> per-owner,
per-channel day buckets.

Pure functions only — no subprocess, no I/O, no printing, no rendering.
Activity is attributed to the repository owner it happened under, so a work
org and personal repositories read as separate sections; within each owner,
four channels (reviews, commits, PRs opened, PRs closed) bucket into one
count per UTC day over the window.

Commits travel a two-step path: :func:`push_groups` chains the feed's push
events per branch, the fetch layer compares each chain, and
:func:`commits_from_compares` keeps your own commits (deduped by sha) with
their real committer dates — so branch work counts the day it happened, not
the day it merged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

DEFAULT_DAYS = 14
MIN_DAYS = 7
MAX_DAYS = 30

CHANNEL_ORDER = ("reviews", "commits", "opened", "closed")


@dataclass(frozen=True)
class RetroChannel:
    """One activity channel: day counts (oldest first, last entry = today),
    their total, and days since the channel's most recent activity (None =
    quiet for the whole window)."""

    label: str
    counts: list[int]
    total: int
    last_days: int | None


@dataclass(frozen=True)
class RetroSection:
    """One repository owner's channels (in ``CHANNEL_ORDER``), plus their
    combined total."""

    owner: str
    channels: list[RetroChannel]
    total: int


@dataclass(frozen=True)
class PushGroup:
    """One branch's pushes within the window, chained into a single
    ``base...head`` range; ``push_stamps`` keeps each push's timestamp for
    the can't-compare fallback."""

    repo: str
    base: str
    head: str
    push_stamps: list[str]


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


def _owner_of(full_name: Any) -> str | None:
    """The owner part of ``OWNER/REPO``."""
    if not isinstance(full_name, str) or "/" not in full_name:
        return None
    return full_name.split("/", 1)[0]


# --- pushes -> commit refs -------------------------------------------------


def push_groups(
    events: list[dict[str, Any]], days: int, now: datetime
) -> list[PushGroup]:
    """The window's push events chained per (repo, branch), oldest base to
    newest head — one compare range per branch instead of one per push."""
    start = window_start(days, now)
    chains: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in events:
        if event.get("type") != "PushEvent":
            continue
        stamp = parse_timestamp(event.get("created_at"))
        if stamp is None or stamp < start:
            continue
        repo_block = event.get("repo")
        repo = repo_block.get("name") if isinstance(repo_block, dict) else None
        payload = event.get("payload")
        if not isinstance(payload, dict) or not isinstance(repo, str):
            continue
        ref = payload.get("ref")
        if not isinstance(ref, str) or not ref:
            continue
        if not all(
            isinstance(payload.get(key), str) and payload[key]
            for key in ("before", "head")
        ):
            continue
        chains.setdefault((repo, ref), []).append(event)
    groups = []
    for (repo, _ref), pushes in chains.items():
        # The feed is newest-first: chain from the oldest push's base to the
        # newest push's head.
        groups.append(
            PushGroup(
                repo=repo,
                base=pushes[-1]["payload"]["before"],
                head=pushes[0]["payload"]["head"],
                push_stamps=[push["created_at"] for push in pushes],
            )
        )
    return groups


def commits_from_compares(
    groups: list[PushGroup],
    compares: list[dict[str, Any] | None],
    login: str,
) -> tuple[list[tuple[str, Any]], int]:
    """``(commit refs, unexpanded push count)`` from the compare payloads.

    Keeps commits you authored, deduped by sha across branches, stamped with
    their committer date. A range that couldn't be compared falls back to one
    commit per push on the push's own day — counted, and reported via the
    second figure so the approximation is never silent.
    """
    refs: list[tuple[str, Any]] = []
    seen: set[str] = set()
    unexpanded = 0
    for group, compare in zip(groups, compares, strict=True):
        owner = _owner_of(group.repo)
        if owner is None:
            continue
        commits = compare.get("commits") if isinstance(compare, dict) else None
        if not isinstance(commits, list):
            unexpanded += len(group.push_stamps)
            refs.extend((owner, stamp) for stamp in group.push_stamps)
            continue
        for item in commits:
            if not isinstance(item, dict):
                continue
            author = item.get("author")
            if not isinstance(author, dict) or author.get("login") != login:
                continue
            sha = item.get("sha")
            if isinstance(sha, str):
                if sha in seen:
                    continue
                seen.add(sha)
            commit = item.get("commit")
            committer = (
                commit.get("committer") if isinstance(commit, dict) else None
            )
            date = committer.get("date") if isinstance(committer, dict) else None
            refs.append((owner, date))
    return refs, unexpanded


# --- channel refs ------------------------------------------------------------


def _pr_ref(item: dict[str, Any], stamp_field: str) -> tuple[str, Any] | None:
    """``(owner, timestamp)`` for one PR-search item (repository_url carries
    ``…/repos/OWNER/REPO``); the timestamp comes from ``stamp_field``."""
    url = item.get("repository_url")
    if not isinstance(url, str) or "/repos/" not in url:
        return None
    owner = _owner_of(url.split("/repos/", 1)[1])
    return (owner, item.get(stamp_field)) if owner else None


def _review_ref(event: dict[str, Any]) -> tuple[str, Any] | None:
    """``(owner, timestamp)`` for one feed event, reviews only."""
    if event.get("type") != "PullRequestReviewEvent":
        return None
    repo = event.get("repo")
    owner = _owner_of(repo.get("name") if isinstance(repo, dict) else None)
    return (owner, event.get("created_at")) if owner else None


def _channel(label: str, ages: list[int], days: int) -> RetroChannel:
    counts = [0] * days
    for age in ages:
        if 0 <= age < days:
            counts[days - 1 - age] += 1
    last_days = next(
        (days - 1 - i for i in range(days - 1, -1, -1) if counts[i]), None
    )
    return RetroChannel(
        label=label, counts=counts, total=sum(counts), last_days=last_days
    )


def build_sections(
    commit_refs: list[tuple[str, Any]],
    opened_items: list[dict[str, Any]],
    closed_items: list[dict[str, Any]],
    events: list[dict[str, Any]],
    days: int,
    now: datetime,
) -> list[RetroSection]:
    """Per-owner sections, most active owner first; quiet owners are dropped.

    ``opened_items`` and ``closed_items`` stay separate because a PR opened
    and closed inside the window comes back from both searches — merging the
    lists would count it twice per channel."""
    today = now.astimezone(UTC).date()
    ages: dict[str, dict[str, list[int]]] = {}

    def add(channel: str, ref: tuple[str, Any] | None) -> None:
        if ref is None:
            return
        owner, raw_timestamp = ref
        timestamp = parse_timestamp(raw_timestamp)
        if timestamp is None:
            return
        owner_ages = ages.setdefault(
            owner, {label: [] for label in CHANNEL_ORDER}
        )
        owner_ages[channel].append(
            (today - timestamp.astimezone(UTC).date()).days
        )

    for ref in commit_refs:
        add("commits", ref)
    for item in opened_items:
        add("opened", _pr_ref(item, "created_at"))
    for item in closed_items:
        add("closed", _pr_ref(item, "closed_at"))
    for event in events:
        add("reviews", _review_ref(event))

    sections = []
    for owner, owner_ages in ages.items():
        channels = [
            _channel(label, owner_ages[label], days) for label in CHANNEL_ORDER
        ]
        total = sum(channel.total for channel in channels)
        if total:
            sections.append(
                RetroSection(owner=owner, channels=channels, total=total)
            )
    return sorted(sections, key=lambda s: (-s.total, s.owner.lower()))


# --- honesty notes ------------------------------------------------------------


def feed_covers_window(
    events: list[dict[str, Any]], days: int, now: datetime, feed_cap: int
) -> bool:
    """Whether the events feed reaches back to the window's start.

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
    """The feed undercount warning, or None when the window is covered.

    Reviews and commits both derive from the feed, so both undercount when
    it can't reach the window's start; opened PRs come from search and are
    unaffected.
    """
    if feed_covers_window(events, days, now, feed_cap):
        return None
    return (
        f"Note: GitHub keeps only your {feed_cap} most recent events; review "
        "and commit counts for early days of this window may be undercounted."
    )


def unexpanded_note(unexpanded: int) -> str | None:
    """The can't-compare fallback warning, or None when nothing fell back."""
    if not unexpanded:
        return None
    plural = "es" if unexpanded != 1 else ""
    return (
        f"Note: {unexpanded} push{plural} could not be expanded into commits "
        "(rewritten history); each counted as one commit on its push day."
    )


def truncation_note(kind: str, fetched: int, total: int) -> str | None:
    """The search-cap warning, or None when everything was retrieved."""
    if fetched >= total:
        return None
    return (
        f"Note: GitHub search returns at most 1000 results; counting "
        f"{fetched} of {total} {kind}."
    )
