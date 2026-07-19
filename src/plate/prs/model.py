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
    """

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
    has_conflicts: bool
    mergeable_unknown: bool
    check_state: str
    original_index: int


@dataclass(frozen=True)
class PrSummary:
    """The counts behind the one-line TLDR — data only, no formatting.

    Formatting (zero-suppression, the ``·``-joined line) lives in the render
    layer; this is just the four figures it needs.
    """

    open: int
    to_review: int
    conflicts: int
    failing_ci: int


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


def bot_name(pr: dict[str, Any]) -> str | None:
    """The display name when the PR author is a bot, else None.

    Detection uses GitHub's own author type plus the two login conventions
    bots appear under (`app/name` from gh, `name[bot]` from the REST side),
    so Renovate, github-actions, pre-commit-ci etc. all get the same
    treatment Dependabot did — and a human named "dependabotfan" does not.
    """
    login = author_login(pr)
    if login is None:
        return None
    author = pr.get("author")
    is_bot = (
        (isinstance(author, dict) and author.get("__typename") == "Bot")
        or login.startswith("app/")
        or login.endswith("[bot]")
    )
    if not is_bot:
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


def age_in_days(pr: dict[str, Any], now: datetime | None) -> int | None:
    if now is None:
        return None
    updated = parse_timestamp(pr.get("updatedAt"))
    if updated is None:
        return None
    return max(0, (now - updated).days)


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
) -> list[PrRow]:
    rows: list[PrRow] = []
    for index, pr in enumerate(prs):
        assignees = assignee_logins(pr)
        is_mine, is_to_review = row_flags(pr, assignees, current_login)
        # Store display-ready names: your own login reads as "me" wherever it
        # appears in the assignee list.
        assignees = ["me" if login == current_login else login for login in assignees]
        age = age_in_days(pr, now)
        rows.append(
            PrRow(
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
                is_stale=age is not None and age >= stale_days,
                age_days=age,
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


def summary_counts(rows: list[PrRow]) -> PrSummary:
    """Count the four figures the one-line TLDR reports — see :class:`PrSummary`.

    The counting lives here; zero-suppression and formatting are the render
    layer's concern.
    """
    return PrSummary(
        open=len(rows),
        to_review=sum(1 for row in rows if sort_group(row) == 1),
        conflicts=sum(1 for row in rows if pr_state(row) == "conflict"),
        failing_ci=sum(1 for row in rows if row.check_state == "failure"),
    )
