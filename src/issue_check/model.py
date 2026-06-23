"""Domain model: raw ``gh`` JSON -> a sorted forest of :class:`IssueRow`s.

Pure functions only — no subprocess, no I/O, no printing. This is where the
tool's logic lives and what the unit tests exercise. The GraphQL shape this
consumes is produced by :mod:`issue_check.github`.

The "yours" slice renders as a **tree**: every issue assigned to you is a node,
shown beneath its parent. Ancestors that are *not* assigned to you are still
materialised as context nodes (rendered dimmed) so a child never floats
parentless. Ordering is *active-first by subtree* — a group sorts by the most
recently touched issue anywhere beneath it, so the cluster you are working in
now rises as a whole unit and stale clusters sink intact. See ``MVP.md``.

Scope note: every *owned* node is assigned to you, hence always owned, so the
full design's ``untriaged`` / ``backlog`` health states cannot occur here;
:func:`issue_state` resolves only to ``active`` / ``stale`` for owned nodes.
Context (un-owned) nodes carry no health state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Labels in the wild carry literal emoji shortcodes (e.g. ":cockroach: bug").
# They are noise in a width-constrained column, so we strip them.
_EMOJI_SHORTCODE_RE = re.compile(r":[a-z0-9_+]+:\s*")

ACTIVE = "active"
STALE = "stale"

# Linked-PR ("fix in flight") states, in the priority used to pick which one to
# surface when an issue links several PRs. Live work outranks a landed or dead
# attempt: an open fix is the thing you care about, a merged PR on a still-open
# issue is a nudge, a closed-unmerged PR is a dead end shown only for context.
PR_OPEN = "open"      # open, ready for review
PR_DRAFT = "draft"    # open, still a draft (WIP)
PR_MERGED = "merged"  # merged, yet the issue is still open
PR_CLOSED = "closed"  # closed without merging — an abandoned attempt
_PR_PRIORITY = [PR_OPEN, PR_DRAFT, PR_MERGED, PR_CLOSED]


@dataclass(frozen=True)
class IssueRow:
    """A single issue node, normalized for rendering. Immutable by design.

    ``mine`` distinguishes an issue assigned to you (full data, health glyph)
    from an ancestor pulled in only for context (``mine=False``: no labels or
    comments, rendered dimmed).
    """

    number: int
    url: str
    title: str
    labels: list[str]
    comments_count: int
    age_days: int | None
    is_stale: bool
    parent_number: int | None
    sub_total: int
    sub_completed: int
    mine: bool
    pr_state: str | None = None   # dominant linked-PR state, or None if unlinked
    pr_number: int | None = None  # the PR backing pr_state (for markdown / links)

    @property
    def has_children(self) -> bool:
        return self.sub_total > 0


@dataclass
class TreeNode:
    """An :class:`IssueRow` placed in the forest, with its ordered children."""

    row: IssueRow
    depth: int
    children: list[TreeNode] = field(default_factory=list)


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_in_days(updated_at: Any, now: datetime | None) -> int | None:
    if now is None:
        return None
    updated = parse_timestamp(updated_at)
    if updated is None:
        return None
    return max(0, (now - updated).days)


def strip_emoji_shortcodes(name: str) -> str:
    return _EMOJI_SHORTCODE_RE.sub("", name).strip()


def compact_text(value: Any) -> str:
    """Collapse a multi-line value to a single spaced line."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _labels(issue: dict[str, Any]) -> list[str]:
    nodes = (issue.get("labels") or {}).get("nodes") or []
    labels: list[str] = []
    for node in nodes:
        name = node.get("name") if isinstance(node, dict) else None
        if isinstance(name, str) and name.strip():
            cleaned = strip_emoji_shortcodes(name)
            if cleaned:
                labels.append(cleaned)
    return labels


def _parent_number(issue: dict[str, Any]) -> int | None:
    parent = issue.get("parent")
    if isinstance(parent, dict) and isinstance(parent.get("number"), int):
        return int(parent["number"])
    return None


def _comments_count(issue: dict[str, Any]) -> int:
    total = (issue.get("comments") or {}).get("totalCount")
    return total if isinstance(total, int) else 0


def _sub_summary(issue: dict[str, Any]) -> tuple[int, int]:
    summary = issue.get("subIssuesSummary") or {}
    total = summary.get("total")
    completed = summary.get("completed")
    return (
        total if isinstance(total, int) else 0,
        completed if isinstance(completed, int) else 0,
    )


def _ancestor_dicts(issue: dict[str, Any]) -> list[dict[str, Any]]:
    """The chain of ``parent`` objects above ``issue``, nearest first."""
    chain: list[dict[str, Any]] = []
    parent = issue.get("parent")
    while isinstance(parent, dict) and isinstance(parent.get("number"), int):
        chain.append(parent)
        parent = parent.get("parent")
    return chain


def _pr_node_state(pr: dict[str, Any]) -> str | None:
    """Map one PR reference to a marker state, or ``None`` if unrecognised."""
    state = pr.get("state")
    if state == "MERGED":
        return PR_MERGED
    if state == "CLOSED":
        return PR_CLOSED
    if state == "OPEN":
        return PR_DRAFT if pr.get("isDraft") else PR_OPEN
    return None


def dominant_pr(issue: dict[str, Any]) -> tuple[str | None, int | None]:
    """The single most-significant linked PR for ``issue``: ``(state, number)``.

    An issue can link several PRs (e.g. one big PR closing many sub-issues, plus
    abandoned earlier attempts). We surface just one marker, chosen by
    :data:`_PR_PRIORITY` — a live open fix beats a merged one beats a dead one.
    Returns ``(None, None)`` when nothing is linked.
    """
    refs = (issue.get("closedByPullRequestsReferences") or {}).get("nodes") or []
    best_rank: int | None = None
    best: tuple[str | None, int | None] = (None, None)
    for pr in refs:
        if not isinstance(pr, dict):
            continue
        state = _pr_node_state(pr)
        if state is None:
            continue
        rank = _PR_PRIORITY.index(state)
        if best_rank is None or rank < best_rank:
            number = pr.get("number")
            best_rank = rank
            best = (state, number if isinstance(number, int) else None)
    return best


def _row_from_issue(
    issue: dict[str, Any],
    now: datetime | None,
    stale_days: int,
    *,
    mine: bool,
) -> IssueRow:
    age = age_in_days(issue.get("updatedAt"), now)
    sub_total, sub_completed = _sub_summary(issue)
    # Context ancestors carry no PR marker — they aren't fetched with PR refs
    # and the marker is a "what's being worked on *my* issue" signal.
    pr_state, pr_number = dominant_pr(issue) if mine else (None, None)
    return IssueRow(
        number=int(issue.get("number", 0)),
        url=str(issue.get("url") or ""),
        title=compact_text(issue.get("title")),
        labels=_labels(issue) if mine else [],
        comments_count=_comments_count(issue) if mine else 0,
        age_days=age,
        is_stale=age is not None and age >= stale_days,
        parent_number=_parent_number(issue),
        sub_total=sub_total,
        sub_completed=sub_completed,
        mine=mine,
        pr_state=pr_state,
        pr_number=pr_number,
    )


def build_index(
    issues: list[dict[str, Any]],
    now: datetime | None,
    stale_days: int,
) -> dict[int, IssueRow]:
    """Map issue number -> :class:`IssueRow` for owned issues *and* ancestors.

    Owned issues (assigned to you) come first and win; each issue's ancestor
    chain then fills in any parent not already present as an un-owned context
    node, so the forest can always be rooted.
    """
    index: dict[int, IssueRow] = {}
    for issue in issues:
        row = _row_from_issue(issue, now, stale_days, mine=True)
        index[row.number] = row
    for issue in issues:
        for ancestor in _ancestor_dicts(issue):
            number = int(ancestor["number"])
            if number not in index:
                index[number] = _row_from_issue(
                    ancestor, now, stale_days, mine=False
                )
    return index


def issue_state(row: IssueRow) -> str:
    """The single health state for an owned node, resolved in priority order.

    Only the two arms reachable in the yours slice are present; the full
    resolver gains ``untriaged`` / ``backlog`` when other groups are built.
    """
    return STALE if row.is_stale else ACTIVE


def progress_text(row: IssueRow) -> str:
    """``completed/total`` sub-issue rollup, or empty when childless."""
    return f"{row.sub_completed}/{row.sub_total}" if row.has_children else ""


def build_forest(index: dict[int, IssueRow]) -> list[TreeNode]:
    """Assemble ``index`` into a sorted forest.

    A node parents another when its number is that node's ``parent_number`` and
    it exists in the index; everything else is a root. Siblings (and roots) are
    ordered *active-subtree first*: by the minimum age anywhere in the subtree,
    ascending, ties broken by issue number. This floats the cluster you are
    working in now to the top as a whole unit, while stale clusters sink intact.
    A node with no age (missing timestamp) contributes nothing, so a subtree of
    only undated nodes sorts last.
    """
    children: dict[int, list[int]] = {}
    roots: list[int] = []
    for number, row in index.items():
        parent = row.parent_number
        if parent is not None and parent in index:
            children.setdefault(parent, []).append(number)
        else:
            roots.append(number)

    # Sentinel age for nodes/subtrees with no datable activity: sorts them last.
    NO_AGE = 10**9
    min_age_cache: dict[int, int] = {}

    def subtree_min_age(number: int) -> int:
        if number in min_age_cache:
            return min_age_cache[number]
        row = index[number]
        best = row.age_days if row.age_days is not None else NO_AGE
        for child in children.get(number, []):
            best = min(best, subtree_min_age(child))
        min_age_cache[number] = best
        return best

    def ordered(numbers: list[int]) -> list[int]:
        return sorted(numbers, key=lambda n: (subtree_min_age(n), n))

    def make(number: int, depth: int) -> TreeNode:
        kids = ordered(children.get(number, []))
        return TreeNode(
            row=index[number],
            depth=depth,
            children=[make(child, depth + 1) for child in kids],
        )

    return [make(root, 0) for root in ordered(roots)]


def flatten(forest: list[TreeNode]) -> list[TreeNode]:
    """Depth-first pre-order traversal — render order."""
    out: list[TreeNode] = []

    def walk(node: TreeNode) -> None:
        out.append(node)
        for child in node.children:
            walk(child)

    for node in forest:
        walk(node)
    return out
