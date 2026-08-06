"""Domain model: raw ``gh`` GraphQL PR nodes -> a list of :class:`PrRow`s.

Pure functions only — no subprocess, no I/O, no printing, and no rendering
(that is the render layer's job). This is the PR-view counterpart to
:mod:`plate.issues.model`: it takes the GraphQL PR payload shape produced by
the fetch layer and normalizes each node into an immutable :class:`PrRow`,
resolving the derived signals the views care about — bot authorship, the
review/CI state mapping, conflict/staleness flags, the single headline
:func:`pr_state`, and the yours / to-review / the-rest grouping.

Ported from the standalone ``gh-pr-status`` tool during the absorption epic
(#50); the boundary test enforces that this module imports only stdlib and
never reaches into :mod:`plate.issues`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

DEFAULT_STALE_DAYS = 14

# release-please titles this repo's release PRs ``chore(main): release 0.5.0``
# (scope = the release branch, suffix = the computed version); the standalone
# gh-pr-status tool matched the older literal ``chore: release main``. This
# pattern accepts both: an optional conventional-commit scope and any text
# after ``release`` (the version), while the ``chore … release`` anchoring
# keeps it from ever matching an ordinary PR.
_RELEASE_PR_RE = re.compile(r"^chore(?:\([^)]*\))?: release\b")


@dataclass(frozen=True)
class PrRow:
    """A single open PR, normalized for the PR views. Immutable by design.

    ``assignees`` holds display-ready logins — your own login reads as ``me``.
    ``is_mine`` / ``is_to_review`` are the grouping flags (see
    :func:`row_flags`); ``original_index`` preserves the fetch order so the
    grouping sort stays stable within each group.

    ``repo`` (``OWNER/REPO``) is the PR's repository. The repo view fills it
    from the ``repo`` argument (its nodes carry no ``repository`` field); the
    owner view reads each node's own ``repository.nameWithOwner`` — that is what
    :func:`group_by_repo` sections on.

    ``age_days`` is days since the PR was opened. ``last_activity_days`` is
    days since the last *human* move across the commit/review/comment channels
    (updatedAt when only bots ever touched it); ``last_activity_mine`` is its
    direction — True you moved last, False another human did, None when no
    direction can be claimed. The per-channel lags are kept for future
    heuristics even though the views render only their max.
    """

    repo: str
    number: int
    url: str
    title: str
    status: str
    assignees: list[str]
    review_status: str
    comments_count: int
    is_mine: bool
    is_to_review: bool
    is_release_pr: bool
    bot_name: str | None
    i_approved: bool
    is_stale: bool
    age_days: int | None
    last_activity_days: int | None
    last_activity_mine: bool | None
    last_commit_days: int | None
    last_review_days: int | None
    last_comment_days: int | None
    has_conflicts: bool
    mergeable_unknown: bool
    check_state: str
    original_index: int


@dataclass(frozen=True)
class PrSummary:
    """The counts behind the one-line TLDR — data only, no formatting.

    Formatting (zero-suppression, the ``·``-joined line) lives in the render
    layer; this is just the five figures it needs. ``your_move`` counts rows
    where the other side moved last on a PR that is yours or to review.
    """

    open: int
    to_review: int
    conflicts: int
    failing_ci: int
    your_move: int


def connection_nodes(value: Any) -> list[dict[str, Any]]:
    """The node dicts of a GraphQL connection ({"nodes": [...]}) or plain list."""
    if isinstance(value, dict):
        value = value.get("nodes")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def compact_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.splitlines())


def truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    if max_length <= 3:
        return value[:max_length]
    return f"{value[: max_length - 3]}..."


def clean_title(title: Any, max_length: int = 50) -> str:
    if not isinstance(title, str):
        return ""
    return truncate(compact_text(title), max_length)


def assignee_logins(pr: dict[str, Any]) -> list[str]:
    logins: list[str] = []
    for assignee in connection_nodes(pr.get("assignees")):
        login = assignee.get("login")
        if isinstance(login, str) and login:
            logins.append(login)
    return logins


def pr_repo(pr: dict[str, Any], default_repo: str) -> str:
    """``OWNER/REPO`` for ``pr``: its own ``repository`` field, else ``default_repo``.

    The owner search fetches ``repository { nameWithOwner }`` on every PR node
    (each can live in a different repo), so that wins when present; the repo
    view's nodes carry no such field, so they fall back to the queried repo.
    """
    repository = pr.get("repository")
    if isinstance(repository, dict):
        name = repository.get("nameWithOwner")
        if isinstance(name, str) and name:
            return name
    return default_repo


def author_login(pr: dict[str, Any]) -> str | None:
    author = pr.get("author")
    if not isinstance(author, dict):
        return None
    login = author.get("login")
    if isinstance(login, str) and login:
        return login
    return None


def i_approved(pr: dict[str, Any], current_login: str | None) -> bool:
    if current_login is None:
        return False
    for review in connection_nodes(pr.get("latestReviews")):
        author = review.get("author")
        login = author.get("login") if isinstance(author, dict) else None
        if login == current_login and review.get("state") == "APPROVED":
            return True
    return False


def is_bot_actor(author: Any) -> bool:
    """Whether an ``author``/actor dict is a bot.

    Detection uses GitHub's own author type plus the two login conventions
    bots appear under (`app/name` from gh, `name[bot]` from the REST side),
    so Renovate, github-actions, pre-commit-ci etc. all get the same
    treatment Dependabot did — and a human named "dependabotfan" does not.
    """
    if not isinstance(author, dict):
        return False
    login = author.get("login")
    if not isinstance(login, str) or not login:
        return False
    return (
        author.get("__typename") == "Bot"
        or login.startswith("app/")
        or login.endswith("[bot]")
    )


def bot_name(pr: dict[str, Any]) -> str | None:
    """The display name when the PR author is a bot, else None."""
    login = author_login(pr)
    if login is None or not is_bot_actor(pr.get("author")):
        return None
    name = login.split("/", 1)[-1]
    if name.endswith("[bot]"):
        name = name[: -len("[bot]")]
    return name


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_since(value: Any, now: datetime | None) -> int | None:
    if now is None:
        return None
    timestamp = parse_timestamp(value)
    if timestamp is None:
        return None
    return max(0, (now - timestamp).days)


def age_in_days(pr: dict[str, Any], now: datetime | None) -> int | None:
    """Days since the PR was opened — total time in flight (the Age column)."""
    return _days_since(pr.get("createdAt"), now)


# --- last-activity channels -----------------------------------------------
#
# One trailing (timestamp, login) event per channel. Bot actors are skipped;
# an event with no actor login counts as activity but claims no direction.


def _last_commit_event(pr: dict[str, Any]) -> tuple[str, str | None] | None:
    commits = connection_nodes(pr.get("commits"))
    commit = commits[0].get("commit") if commits else None
    if not isinstance(commit, dict):
        return None
    date = commit.get("committedDate")
    if not isinstance(date, str) or not date:
        return None
    author = commit.get("author")
    user = author.get("user") if isinstance(author, dict) else None
    if is_bot_actor(user):
        return None
    login = user.get("login") if isinstance(user, dict) else None
    return date, login if isinstance(login, str) and login else None


def _last_actor_event(
    pr: dict[str, Any], connection: str, timestamp_field: str
) -> tuple[str, str | None] | None:
    nodes = connection_nodes(pr.get(connection))
    if not nodes:
        return None
    node = nodes[0]
    date = node.get(timestamp_field)
    if not isinstance(date, str) or not date:
        return None
    author = node.get("author")
    if is_bot_actor(author):
        return None
    login = author.get("login") if isinstance(author, dict) else None
    return date, login if isinstance(login, str) and login else None


def last_human_activity(
    pr: dict[str, Any], now: datetime | None
) -> tuple[dict[str, int | None], int | None, str | None, bool]:
    """The per-channel lags and the winning last human move.

    Returns ``(channel_days, last_days, last_login, is_fallback)``. With no
    human event at all (a bot-only PR), ``last_days`` falls back to updatedAt
    and ``is_fallback`` is True so callers claim no direction.
    """
    events = {
        "commit": _last_commit_event(pr),
        "review": _last_actor_event(pr, "reviews", "submittedAt"),
        "comment": _last_actor_event(pr, "comments", "createdAt"),
    }
    channel_days = {
        channel: _days_since(event[0], now) if event else None
        for channel, event in events.items()
    }
    dated: list[tuple[datetime, str | None]] = []
    for event in events.values():
        if event is None:
            continue
        timestamp = parse_timestamp(event[0])
        if timestamp is not None:
            dated.append((timestamp, event[1]))
    if not dated:
        return channel_days, _days_since(pr.get("updatedAt"), now), None, True
    latest, login = max(dated, key=lambda item: item[0])
    days = max(0, (now - latest).days) if now is not None else None
    return channel_days, days, login, False


def has_conflicts(pr: dict[str, Any]) -> bool:
    return pr.get("mergeable") == "CONFLICTING"


def check_state(pr: dict[str, Any]) -> str:
    """Map GitHub's own status-check rollup to none/failure/pending/success.

    The rollup state on the head commit is the exact verdict the GitHub UI
    shows, so no client-side aggregation of individual checks is needed.
    """
    commits = connection_nodes(pr.get("commits"))
    commit = commits[0].get("commit") if commits else None
    rollup = commit.get("statusCheckRollup") if isinstance(commit, dict) else None
    state = rollup.get("state") if isinstance(rollup, dict) else None
    if state in ("FAILURE", "ERROR"):
        return "failure"
    if state in ("PENDING", "EXPECTED"):
        return "pending"
    if state == "SUCCESS":
        return "success"
    return "none"


def comments_count(pr: dict[str, Any]) -> int:
    # totalCommentsCount is GitHub's badge figure: issue comments + review
    # summaries + inline review comments — what the UI speech bubble shows.
    total = pr.get("totalCommentsCount")
    return total if isinstance(total, int) else 0


def review_status(review_decision: Any) -> str:
    if review_decision == "APPROVED":
        return "approved"
    if review_decision == "CHANGES_REQUESTED":
        return "pending (changes requested)"
    return "pending"


def is_release_pr(title: Any) -> bool:
    """Whether ``title`` is a release-please release PR — see :data:`_RELEASE_PR_RE`."""
    return isinstance(title, str) and _RELEASE_PR_RE.match(title) is not None


def row_flags(
    pr: dict[str, Any], assignees: list[str], current_login: str | None
) -> tuple[bool, bool]:
    # A PR is yours if you wrote it or you're assigned — teams that don't
    # self-assign must not see their own PRs land in "to review".
    is_mine = current_login is not None and (
        current_login == author_login(pr) or current_login in assignees
    )
    is_published = not bool(pr.get("isDraft"))
    is_approved = pr.get("reviewDecision") == "APPROVED"
    is_to_review = (
        current_login is not None and not is_mine and is_published and not is_approved
    )
    return is_mine, is_to_review


def normalize_rows(
    prs: list[dict[str, Any]],
    current_login: str | None,
    now: datetime | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
    *,
    repo: str = "",
) -> list[PrRow]:
    """Normalize raw PR nodes into :class:`PrRow`s, preserving fetch order.

    ``repo`` is the fallback ``OWNER/REPO`` for a node that carries no
    ``repository`` field of its own (the repo view's nodes): the repo-view call
    site passes the known repo, the owner view lets each node's own
    ``repository.nameWithOwner`` win (see :func:`pr_repo`).
    """
    rows: list[PrRow] = []
    for index, pr in enumerate(prs):
        assignees = assignee_logins(pr)
        is_mine, is_to_review = row_flags(pr, assignees, current_login)
        # Store display-ready names: your own login reads as "me" wherever it
        # appears in the assignee list.
        assignees = ["me" if login == current_login else login for login in assignees]
        channel_days, last_days, last_login, is_fallback = last_human_activity(
            pr, now
        )
        # Viewer-relative; unknown actor or unknown viewer claims nothing.
        last_mine: bool | None = None
        if not is_fallback and last_login is not None and current_login is not None:
            last_mine = last_login == current_login
        rows.append(
            PrRow(
                repo=pr_repo(pr, repo),
                number=int(pr.get("number", 0)),
                url=str(pr.get("url") or ""),
                title=clean_title(pr.get("title")),
                status="draft" if pr.get("isDraft") else "published",
                assignees=assignees,
                review_status=review_status(pr.get("reviewDecision")),
                comments_count=comments_count(pr),
                is_mine=is_mine,
                is_to_review=is_to_review,
                is_release_pr=is_release_pr(pr.get("title")),
                bot_name=bot_name(pr),
                i_approved=i_approved(pr, current_login),
                # Staleness anchors on the last human move, not tenure.
                is_stale=last_days is not None and last_days >= stale_days,
                age_days=age_in_days(pr, now),
                last_activity_days=last_days,
                last_activity_mine=last_mine,
                last_commit_days=channel_days["commit"],
                last_review_days=channel_days["review"],
                last_comment_days=channel_days["comment"],
                has_conflicts=has_conflicts(pr),
                mergeable_unknown=pr.get("mergeable") == "UNKNOWN",
                check_state=check_state(pr),
                original_index=index,
            )
        )
    return rows


def pr_state(row: PrRow) -> str:
    """The single headline state, resolved in priority order.

    Failing CI and requested changes are deliberately not headline states — they
    already surface in the CI and Review columns, so the leading glyph is
    reserved for what isn't otherwise visible.
    """
    if row.status == "draft":
        return "draft"
    if row.has_conflicts:
        return "conflict"
    if row.review_status == "approved" and row.check_state in ("success", "none"):
        # GitHub reports mergeable=UNKNOWN while recomputing after a push.
        # "ready" is a merge-this-now claim, so don't make it unconfirmed.
        return "unknown" if row.mergeable_unknown else "ready"
    return "waiting"


def sort_group(row: PrRow) -> int:
    if row.is_mine:
        return 0
    if row.is_to_review:
        return 1
    return 2


def sort_key(row: PrRow) -> tuple[int, int]:
    return (sort_group(row), row.original_index)


def your_move(row: PrRow) -> bool:
    """Another human moved last on a PR that is yours or to review."""
    return row.last_activity_mine is False and (row.is_mine or row.is_to_review)


def summary_counts(rows: list[PrRow]) -> PrSummary:
    """Count the five figures the one-line TLDR reports — see :class:`PrSummary`.

    The counting lives here; zero-suppression and formatting are the render
    layer's concern.
    """
    return PrSummary(
        open=len(rows),
        to_review=sum(1 for row in rows if sort_group(row) == 1),
        conflicts=sum(1 for row in rows if pr_state(row) == "conflict"),
        failing_ci=sum(1 for row in rows if row.check_state == "failure"),
        your_move=sum(1 for row in rows if your_move(row)),
    )


def group_by_repo(rows: list[PrRow]) -> list[tuple[str, list[PrRow]]]:
    """Partition ``rows`` by repository for the owner-wide view.

    Repos appear most-recently-active first, and rows within a repo keep fetch
    order — both fall out of preserving ``rows``' order. The owner fetch is
    ``sort:updated-desc``, so a repo's first-seen row is its most recently
    active, and first-appearance order across repos is therefore activity order;
    Python dicts preserve insertion order, so grouping without re-sorting keeps
    exactly that. Unlike the issues ``group_by_repo`` (which rebuilds a forest
    per repo and sorts by subtree age), the PR view is a flat list, so ordering
    is entirely carried by the server's sort.
    """
    grouped: dict[str, list[PrRow]] = {}
    for row in rows:
        grouped.setdefault(row.repo, []).append(row)
    return list(grouped.items())
