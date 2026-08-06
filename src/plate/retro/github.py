"""Retro-domain GitHub fetch: the viewer's own REST events feed.

One's own feed includes private-repository events, which is the reason this
domain reads REST rather than GraphQL's contributionsCollection: the latter
itemizes public activity only — private work is folded into an opaque
restrictedContributionsCount, even for the authenticated user. Builds on
:mod:`plate.core.gh` for the shared plumbing; no other retro-domain module
shells out.
"""

from __future__ import annotations

import json
from typing import Any

from plate.core import gh

EVENTS_PER_PAGE = 100
# GitHub retains at most the 300 most recent events (and none older than 90
# days). Pages are fetched one by one — asking for a page past the cap is a
# hard API error, so pagination stops at the cap or the first short page.
EVENTS_MAX_PAGES = 3
EVENTS_FEED_CAP = EVENTS_PER_PAGE * EVENTS_MAX_PAGES


def fetch_events(login: str) -> list[dict[str, Any]]:
    """The viewer's recent public+private activity events, newest first.

    Raises :class:`~plate.core.gh.PlateError` when ``gh`` fails or a page
    isn't shaped as expected.
    """
    events: list[dict[str, Any]] = []
    for page in range(1, EVENTS_MAX_PAGES + 1):
        result = gh.run_command(
            [
                "gh",
                "api",
                f"users/{login}/events?per_page={EVENTS_PER_PAGE}&page={page}",
            ]
        )
        if result.returncode != 0:
            raise gh.PlateError(
                "gh failed to fetch your activity feed "
                f"(is `gh` authenticated?):\n{result.stderr.strip()}"
            )
        try:
            batch = json.loads(result.stdout)
        except ValueError as exc:
            raise gh.PlateError(f"Could not parse gh JSON: {exc}") from exc
        if not isinstance(batch, list):
            raise gh.PlateError("GitHub returned an unexpected events payload.")
        events.extend(event for event in batch if isinstance(event, dict))
        if len(batch) < EVENTS_PER_PAGE:
            break
    return events
