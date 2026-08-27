"""Retro-domain GitHub fetches: the events feed, PR search, and push compares.

Each channel reads the best source that can see private activity — GraphQL's
contributionsCollection cannot (private work collapses into an opaque
restrictedContributionsCount even for the authenticated user):

- reviews: the viewer's own events feed — the one source carrying review
  timestamps;
- PRs opened: the issue search API (``is:pr``), as the owner views already use;
- commits: the feed's push events expanded through the compare API — commit
  search was probed and rejected: it indexes default branches only, so branch
  work stays invisible until merge.

Builds on :mod:`plate.core.gh` for the shared plumbing; no other retro-domain
module shells out.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from plate.core import gh

EVENTS_PER_PAGE = 100
# GitHub retains at most the 300 most recent events (and none older than 90
# days). Pages are fetched one by one — asking for a page past the cap is a
# hard API error, so pagination stops at the cap or the first short page.
EVENTS_MAX_PAGES = 3
EVENTS_FEED_CAP = EVENTS_PER_PAGE * EVENTS_MAX_PAGES

SEARCH_PER_PAGE = 100
SEARCH_MAX_PAGES = 10  # any search caps at 1000 results

COMPARE_WORKERS = 8


def _report_retry(status: str, attempt: int) -> None:
    if attempt < gh.MAX_ATTEMPTS:
        gh.progress(
            f"GitHub answered HTTP {status} — retrying "
            f"(attempt {attempt + 1}/{gh.MAX_ATTEMPTS})…"
        )


def _fetch_json(path: str) -> Any:
    attempt = gh.run_gh_with_retry(
        lambda: ["gh", "api", path], on_transient=_report_retry
    )
    if attempt.exhausted:
        raise gh.PlateError(
            f"gh failed to fetch your activity: GitHub answered HTTP "
            f"{attempt.status} on {attempt.attempts} attempts.\n"
            "That status is GitHub failing the request server-side — it "
            "happens intermittently. Wait a moment and rerun; if it "
            "persists, try a shorter --days window."
        )
    result = attempt.result
    if result.returncode != 0:
        raise gh.PlateError(
            "gh failed to fetch your activity "
            f"(is `gh` authenticated?):\n{result.stderr.strip()}"
            + gh.rate_limit_hint(result.stderr, "use a shorter --days window")
        )
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise gh.PlateError(f"Could not parse gh JSON: {exc}") from exc


def fetch_events(login: str) -> list[dict[str, Any]]:
    """The viewer's recent public+private activity events, newest first."""
    events: list[dict[str, Any]] = []
    for page in range(1, EVENTS_MAX_PAGES + 1):
        gh.progress(f"Fetching your GitHub events (page {page}/{EVENTS_MAX_PAGES})…")
        batch = _fetch_json(
            f"users/{login}/events?per_page={EVENTS_PER_PAGE}&page={page}"
        )
        if not isinstance(batch, list):
            raise gh.PlateError("GitHub returned an unexpected events payload.")
        events.extend(event for event in batch if isinstance(event, dict))
        if len(batch) < EVENTS_PER_PAGE:
            break
    return events


def _search_prs(
    login: str, qualifiers: str, describe: str
) -> tuple[list[dict[str, Any]], int]:
    """One paginated PR search for ``login``: ``(items, server total)``.
    ``total`` can exceed what pagination retrieves (the 1000-result cap);
    callers compare to report truncation honestly. ``describe`` names the
    channel on the progress line."""
    items: list[dict[str, Any]] = []
    total = 0
    for page in range(1, SEARCH_MAX_PAGES + 1):
        suffix = f" (page {page})" if page > 1 else ""
        gh.progress(f"Searching {describe}{suffix}…")
        payload = _fetch_json(
            f"search/issues?q=author:{login}+is:pr+{qualifiers}"
            f"&per_page={SEARCH_PER_PAGE}&page={page}"
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise gh.PlateError("GitHub returned an unexpected search payload.")
        count = payload.get("total_count")
        if isinstance(count, int):  # a later page missing it keeps page 1's
            total = count
        items.extend(item for item in payload["items"] if isinstance(item, dict))
        if len(payload["items"]) < SEARCH_PER_PAGE:
            break
    return items, total


def fetch_opened(login: str, since_date: str) -> tuple[list[dict[str, Any]], int]:
    """PRs opened by ``login`` since ``since_date`` (YYYY-MM-DD)."""
    return _search_prs(login, f"created:>={since_date}", "PRs opened")


def fetch_closed(login: str, since_date: str) -> tuple[list[dict[str, Any]], int]:
    """PRs by ``login`` closed since ``since_date`` (YYYY-MM-DD) — merged and
    closed-without-merge alike; the channel means "left the plate"."""
    return _search_prs(login, f"is:closed+closed:>={since_date}", "PRs closed")


def _fetch_compare(repo: str, base: str, head: str) -> dict[str, Any] | None:
    """One ``base...head`` comparison, or None when it can't be resolved
    (force-pushed or garbage-collected shas) — the caller falls back. A
    transient 5xx is retried before giving up, so a flaky page doesn't quietly
    cost a branch's commits."""
    attempt = gh.run_gh_with_retry(
        lambda: ["gh", "api", f"repos/{repo}/compare/{base}...{head}"]
    )
    result = attempt.result
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def fetch_compares(
    ranges: list[tuple[str, str, str]],
) -> list[dict[str, Any] | None]:
    """The compare payloads for ``(repo, base, head)`` ranges, order-aligned.

    Ranges are independent, so they run in parallel — one push chain per
    branch keeps this to a handful of requests, not one per push.
    """
    if not ranges:
        return []
    branches = "branch" if len(ranges) == 1 else "branches"
    gh.progress(f"Expanding pushes on {len(ranges)} {branches}…")
    with ThreadPoolExecutor(max_workers=min(COMPARE_WORKERS, len(ranges))) as executor:
        return list(executor.map(lambda r: _fetch_compare(*r), ranges))
