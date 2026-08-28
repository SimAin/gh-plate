"""Retro-domain GitHub fetches: the events feed, PR search, and push compares.

Each channel reads the best source that can see private activity — GraphQL's
contributionsCollection cannot (private work collapses into an opaque
restrictedContributionsCount even for the authenticated user):

- reviews: the viewer's own events feed — the one source carrying review
  timestamps;
- PRs opened: the issue search API (``is:pr``), as the owner views already use;
- commits: the feed's push events expanded through the compare API, unioned
  with a listing of each touched branch's own recent commits — the feed
  carries no push for a branch's creation and drops ordinary pushes too.
  Commit search was probed and rejected: it indexes default branches only,
  so branch work stays invisible until merge.

Builds on :mod:`plate.core.gh` for the shared plumbing; no other retro-domain
module shells out.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

from plate.core import gh

EVENTS_PER_PAGE = 100
# GitHub retains at most the 300 most recent events (and none older than 90
# days). Pages are fetched one by one — asking for a page past the cap is a
# hard API error, so pagination stops at the cap or the first short page.
EVENTS_MAX_PAGES = 3
EVENTS_FEED_CAP = EVENTS_PER_PAGE * EVENTS_MAX_PAGES

SEARCH_PER_PAGE = 100
SEARCH_MAX_PAGES = 10  # any search caps at 1000 results

COMMITS_PER_PAGE = 100
COMMITS_MAX_PAGES = 10  # 1000 of your own commits on one branch in 30 days

BRANCH_WORKERS = 8


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
        suffix = f" (page {page})" if page > 1 else ""
        gh.progress(f"Fetching your GitHub events{suffix}…")
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


def _fetch_quietly(path: str) -> Any | None:
    """``gh api path`` as JSON, or None on any failure — for the per-branch
    fetches, where the caller falls back rather than aborting the retro. A
    transient 5xx is retried before giving up, so a flaky page doesn't quietly
    cost a branch's commits."""
    attempt = gh.run_gh_with_retry(lambda: ["gh", "api", path])
    result = attempt.result
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def _fetch_compare(repo: str, base: str, head: str) -> dict[str, Any] | None:
    """One ``base...head`` comparison, or None when it can't be resolved
    (force-pushed or garbage-collected shas)."""
    payload = _fetch_quietly(f"repos/{repo}/compare/{base}...{head}")
    return payload if isinstance(payload, dict) else None


def _fetch_branch_commits(
    repo: str, branch: str, login: str, since: str
) -> list[dict[str, Any]] | None:
    """``login``'s commits reachable from ``branch`` since ``since``, or None
    when the branch can't be listed (deleted after merge, typically). A page
    failing after the first costs only its own commits, not the listing."""
    commits: list[dict[str, Any]] = []
    for page in range(1, COMMITS_MAX_PAGES + 1):
        payload = _fetch_quietly(
            f"repos/{repo}/commits?sha={quote(branch, safe='')}&author={login}"
            f"&since={since}&per_page={COMMITS_PER_PAGE}&page={page}"
        )
        if not isinstance(payload, list):
            return commits if page > 1 else None
        commits.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < COMMITS_PER_PAGE:
            break
    return commits


def _in_parallel(
    label: str, jobs: list[tuple[Any, ...] | None], fetch: Callable[..., Any]
) -> list[Any]:
    """``fetch(*job)`` for each job, order-aligned; a None job yields None
    without a request. Jobs are independent, so they run in parallel."""
    real = [job for job in jobs if job is not None]
    if not real:
        return [None] * len(jobs)
    branches = "branch" if len(real) == 1 else "branches"
    gh.progress(f"{label} on {len(real)} {branches}…")
    with ThreadPoolExecutor(max_workers=min(BRANCH_WORKERS, len(real))) as executor:
        fetched = iter(executor.map(lambda job: fetch(*job), real))
        return [None if job is None else next(fetched) for job in jobs]


def fetch_compares(
    ranges: list[tuple[str, str, str] | None],
) -> list[dict[str, Any] | None]:
    """The compare payloads for ``(repo, base, head)`` ranges, order-aligned;
    a None range (a branch the feed saw created but never pushed) yields
    None. One push chain per branch keeps this to a handful of requests, not
    one per push."""
    return _in_parallel("Expanding pushes", ranges, _fetch_compare)


def fetch_branch_commits(
    branches: list[tuple[str, str] | None], login: str, since: str
) -> list[list[dict[str, Any]] | None]:
    """``login``'s commits since ``since`` (ISO 8601) on each ``(repo,
    branch)``, order-aligned; a None entry (a tag or other non-branch ref)
    yields None. This is what catches the pushes the events feed never
    carried."""
    return _in_parallel(
        "Listing commits",
        branches,
        lambda repo, branch: _fetch_branch_commits(repo, branch, login, since),
    )
