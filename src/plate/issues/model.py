"""Domain model: raw ``gh`` JSON -> a sorted forest of :class:`IssueRow`s.

Pure functions only — no subprocess, no I/O, no printing. This is where the
tool's logic lives and what the unit tests exercise. The GraphQL shape this
consumes is produced by :mod:`plate.issues.github`.

The "yours" slice renders as a **tree**: every issue assigned to you is a node,
shown beneath its parent. Ancestors that are *not* assigned to you are still
materialised as context nodes (rendered dimmed) so a child never floats
parentless. Ordering is *active-first by subtree* — a group sorts by the most
recently touched issue anywhere beneath it, so the cluster you are working in
now rises as a whole unit and stale clusters sink intact.

Scope note: every *owned* node is assigned to you, hence always owned, so the
full design's ``untriaged`` / ``backlog`` health states cannot occur here;
:func:`issue_state` resolves only to ``active`` / ``stale`` for owned nodes.
Context (un-owned) nodes carry no health state.

Identity is repository-qualified: an issue *number* is only unique within its
own repo, so ``repo-a#12`` and ``repo-b#12`` are different issues that must
never collide in one index. :data:`IssueKey` — ``(repo, number)`` — is what
the index and forest actually key on; this is groundwork for an owner-wide
view spanning several repos (issue #43), not yet wired into the CLI. GitHub
itself allows a sub-issue to live in a different repo than its parent, but
this tool's hierarchy is deliberately **repository-local**: :func:`build_index`
refuses to materialise an ancestor whose repo differs from the owned issue's,
so a cross-repo child simply renders as a root rather than nesting under a
parent from another repo's tree.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from plate.core.render import compact_text

# ``(repo, number)`` — the only identity that is unique across repos. An issue
# *number* alone is only unique within its own repo, so every index/forest in
# this module keys on this pair rather than on a bare number.
IssueKey = tuple[str, int]

# Labels in the wild carry literal emoji shortcodes (e.g. ":cockroach: bug").
# They are noise in a width-constrained column, so we strip them.
_EMOJI_SHORTCODE_RE = re.compile(r":[a-z0-9_+-]+:\s*")

ACTIVE = "active"
STALE = "stale"

# Row classes for the owner-wide view (issue #43), where ALL open issues are
# shown — not just yours — so "mine" and "shown-for-context-only" stop being the
# same axis. :func:`row_class` resolves a row to exactly one of these.
ROW_MINE = "mine"  # assigned to you — full weight, health glyph
ROW_CONTEXT = "context"  # ancestor pulled in only for structure — dimmed
ROW_UNASSIGNED = "unassigned"  # open, nobody assigned — full weight (yours to grab)
ROW_OTHERS = "others"  # assigned to someone else — dimmed whole

# Linked-PR ("fix in flight") states, in the priority used to pick which one to
# surface when an issue links several PRs. Live work outranks a landed or dead
# attempt: an open fix is the thing you care about, a merged PR on a still-open
# issue is a nudge, a closed-unmerged PR is a dead end shown only for context.
PR_OPEN = "open"  # open, ready for review
PR_DRAFT = "draft"  # open, still a draft (WIP)
PR_MERGED = "merged"  # merged, yet the issue is still open
PR_CLOSED = "closed"  # closed without merging — an abandoned attempt
_PR_PRIORITY = [PR_OPEN, PR_DRAFT, PR_MERGED, PR_CLOSED]


@dataclass(frozen=True)
class IssueRow:
    """A single issue node, normalized for rendering. Immutable by design.

    ``context`` marks an ancestor materialised only for structure by
    :func:`build_index`'s ancestor walk (no labels/comments/PR/assignee data,
    rendered dimmed). ``mine`` marks an issue assigned to you. In the yours view
    these are exact opposites (every fetched row is yours, every ancestor is
    not), but the owner-wide view (issue #43) shows all open issues, so a fetched
    row can be ``mine=False, context=False`` (someone else's, or unassigned) —
    the two flags are set independently. See :func:`row_class`.

    ``repo`` (``OWNER/REPO``) is what makes ``(repo, number)`` — see
    :data:`IssueKey` — a safe index key across repos; ``number`` alone is not.
    ``assignees`` is the issue's assignee logins; it is ``[]`` when the query
    that produced this row didn't fetch assignees (true of the yours-view
    query today — it doesn't need them, since every owned issue is assigned to
    you by construction) or when the issue genuinely has none.
    """

    repo: str
    number: int
    url: str
    title: str
    labels: list[str]
    labels_hidden: int  # labels beyond the fetched page (the +N tail)
    comments_count: int
    age_days: int | None
    is_stale: bool
    parent_number: int | None
    sub_total: int
    sub_completed: int
    mine: bool
    context: bool = False
    assignees: list[str] = field(default_factory=list)
    pr_state: str | None = None  # dominant linked-PR state, or None if unlinked
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
    return compact_text(_EMOJI_SHORTCODE_RE.sub("", name))


def _repo_of(issue: dict[str, Any], default_repo: str) -> str:
    """``OWNER/REPO`` for ``issue``: its own ``repository`` field, else the fallback.

    Mirrors the module's other defensive dict parsing: a node's own
    ``repository.nameWithOwner`` wins when present (this is how the cross-repo
    guard in :func:`build_index` detects a sub-issue living in another repo);
    otherwise ``default_repo`` (the repo that was actually queried) applies —
    the yours-view query doesn't currently request ``repository`` on the
    top-level owned issues, since they're all known to live in the queried
    repo already.
    """
    repository = issue.get("repository")
    if isinstance(repository, dict):
        name = repository.get("nameWithOwner")
        if isinstance(name, str) and name:
            return name
    return default_repo


def _assignees(issue: dict[str, Any]) -> list[str]:
    """Assignee logins from ``issue["assignees"]["nodes"]``, or ``[]``.

    Shared by the yours-view rows and the sprint-board rows below — both
    payload shapes carry assignees the same way.
    """
    nodes = (issue.get("assignees") or {}).get("nodes") or []
    return [
        node["login"]
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("login"), str)
    ]


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


def _labels_hidden(issue: dict[str, Any]) -> int:
    """Labels never fetched: ``totalCount`` minus the raw nodes returned.

    Counted against the raw node count (before emoji/hidden filtering), since
    ``totalCount`` is GitHub's raw label count. Absent ``totalCount`` -> 0, so
    payloads/fixtures predating the field keep their behaviour.
    """
    connection = issue.get("labels") or {}
    total = connection.get("totalCount")
    if not isinstance(total, int):
        return 0
    return max(0, total - len(connection.get("nodes") or []))


def _parent_number(issue: dict[str, Any], repo: str) -> int | None:
    """The number of ``issue``'s parent — but only when it lives in ``repo``.

    A cross-repo parent is treated as no parent at all: a bare number from
    another repo is meaningless (and dangerous) in a repo-qualified index,
    because ``build_forest`` reconstructs the parent key as
    ``(row.repo, parent_number)`` — if ``repo`` happened to also contain an
    unrelated issue with that number, the child would be linked under it, a
    false hierarchy from exactly the number-collision class :data:`IssueKey`
    exists to eliminate. A parent payload without a ``repository`` field falls
    back to ``repo`` via :func:`_repo_of` (i.e. same-repo), so payloads that
    don't carry the field behave as before.
    """
    parent = issue.get("parent")
    if isinstance(parent, dict) and isinstance(parent.get("number"), int):
        if _repo_of(parent, repo) == repo:
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
    context: bool,
    mine: bool,
    default_repo: str,
) -> IssueRow:
    """Normalize one issue payload into an :class:`IssueRow`.

    ``context`` and ``mine`` are set independently by the caller (they are exact
    opposites only in the yours view): the ancestor-walk path passes
    ``context=True, mine=False``, the fetched path ``context=False`` with ``mine``
    computed by :func:`build_index`. Structure-only context rows carry no
    labels/comments/PR/assignee data — none of it is fetched for them, and none
    is the signal a dimmed context node is meant to show.
    """
    repo = _repo_of(issue, default_repo)
    age = age_in_days(issue.get("updatedAt"), now)
    sub_total, sub_completed = _sub_summary(issue)
    pr_state, pr_number = (None, None) if context else dominant_pr(issue)
    return IssueRow(
        repo=repo,
        number=int(issue.get("number", 0)),
        url=str(issue.get("url") or ""),
        title=compact_text(issue.get("title")),
        labels=[] if context else _labels(issue),
        labels_hidden=0 if context else _labels_hidden(issue),
        comments_count=0 if context else _comments_count(issue),
        age_days=age,
        is_stale=age is not None and age >= stale_days,
        # None for a cross-repo parent (see _parent_number) — the row must not
        # carry a bare number that could collide with an unrelated same-number
        # issue in its own repo.
        parent_number=_parent_number(issue, repo),
        sub_total=sub_total,
        sub_completed=sub_completed,
        mine=mine,
        context=context,
        assignees=[] if context else _assignees(issue),
        pr_state=pr_state,
        pr_number=pr_number,
    )


def build_index(
    issues: list[dict[str, Any]],
    now: datetime | None,
    stale_days: int,
    *,
    repo: str,
    login: str | None = None,
) -> dict[IssueKey, IssueRow]:
    """Map :data:`IssueKey` (``repo``, ``number``) -> :class:`IssueRow`.

    Fetched issues come first and win, keyed by their own ``(row.repo,
    row.number)`` — ``repo`` is the repo that was queried, used as the fallback
    when an issue's payload carries no ``repository`` field of its own (true of
    the yours-view query today, see :func:`_repo_of`). Each issue's ancestor
    chain then fills in any parent not already present, as an un-owned context
    node, so the forest can always be rooted.

    ``login`` selects how ``mine`` is computed for the fetched rows:

    - ``None`` (the yours view): every fetched issue is ``mine=True`` — the
      search query already filtered to your assignments, and the
      ``assignees(first: 10)`` clipping means membership testing here would be
      less reliable than the query's own guarantee.
    - given (the owner view): a fetched issue is ``mine = login in assignees``,
      since the owner view fetches everyone's issues and must tell yours apart.

    **Cross-repo guard:** GitHub allows a sub-issue's parent to live in a
    different repo, but this tool's hierarchy is repository-local by design —
    an owner-wide view (issue #43) must be able to trust that a repo's tree
    only ever nests issues from that same repo. So while walking an owned
    issue's ancestor chain (nearest first), each ancestor's repo is compared to
    the owned issue's repo (the ancestor's own ``repository.nameWithOwner`` if
    present, else the owned issue's repo, via :func:`_repo_of`). The first
    ancestor whose repo differs is *not* materialised, and the walk stops right
    there rather than continuing further up — so that child renders as a root
    rather than nesting under a parent from another repo's tree. The child's
    row also records ``parent_number=None`` for a cross-repo parent (see
    :func:`_parent_number`), so its bare parent number can never be mistaken
    for an unrelated same-number issue in its own repo.
    """
    index: dict[IssueKey, IssueRow] = {}
    owned: list[tuple[dict[str, Any], IssueRow]] = []
    for issue in issues:
        mine = True if login is None else login in _assignees(issue)
        row = _row_from_issue(
            issue, now, stale_days, context=False, mine=mine, default_repo=repo
        )
        index[(row.repo, row.number)] = row
        owned.append((issue, row))
    for issue, owned_row in owned:
        owned_repo = owned_row.repo
        for ancestor in _ancestor_dicts(issue):
            ancestor_repo = _repo_of(ancestor, owned_repo)
            if ancestor_repo != owned_repo:
                break  # cross-repo guard: stop walking, don't materialize
            key = (ancestor_repo, int(ancestor["number"]))
            if key not in index:
                index[key] = _row_from_issue(
                    ancestor,
                    now,
                    stale_days,
                    context=True,
                    mine=False,
                    default_repo=owned_repo,
                )
    return index


def issue_state(row: IssueRow) -> str:
    """The single health state for an owned node, resolved in priority order.

    Only the two arms reachable in the yours slice are present; the full
    resolver gains ``untriaged`` / ``backlog`` when other groups are built.
    """
    return STALE if row.is_stale else ACTIVE


def row_class(row: IssueRow) -> str:
    """Classify ``row`` for the owner-wide view's four rendering weights.

    The owner view (issue #43) shows every open issue across an owner's repos,
    not just yours, so a single "mine vs. not" split no longer captures how a row
    should read. Four classes, each with its own weight:

    - ``mine`` / ``unassigned`` render full-weight with a health glyph. Yours is
      obvious; unassigned is deliberate — in a personal project, unassigned work
      is still yours to pick up, so it earns the same attention as your own.
    - ``others`` (assigned to someone else) is dimmed whole — the sprint view's
      weight-is-attention precedent: it's on screen for awareness, not action.
    - ``context`` is a structure-only ancestor (see :func:`build_index`), carrying
      no data of its own; it exists so a child never floats parentless.

    Checked context-first: a ``context`` ancestor is never ``mine`` and has no
    assignees, so its class must win before the assignment-based arms.
    """
    if row.context:
        return ROW_CONTEXT
    if row.mine:
        return ROW_MINE
    if not row.assignees:
        return ROW_UNASSIGNED
    return ROW_OTHERS


def progress_text(row: IssueRow) -> str:
    """``completed/total`` sub-issue rollup, or empty when childless."""
    return f"{row.sub_completed}/{row.sub_total}" if row.has_children else ""


def build_forest(index: dict[IssueKey, IssueRow]) -> list[TreeNode]:
    """Assemble ``index`` into a sorted forest.

    A node parents another when ``(row.repo, row.parent_number)`` — its
    parent's :data:`IssueKey` — exists in the index; everything else is a
    root. Trees stay repository-local because ``parent_number`` is already
    ``None`` when the real parent lives in another repo (nulled at
    row-construction time, see :func:`_parent_number`) — so a cross-repo
    child can never link under an unrelated same-number issue in its own
    repo. The repo-qualified key lookup here, combined with the cross-repo
    guard in :func:`build_index`, is belt-and-braces on top of that.

    Siblings (and roots) are ordered *active-subtree first*: by the minimum
    age anywhere in the subtree, ascending, ties broken by the full
    ``(repo, number)`` key. This floats the cluster you are working in now to
    the top as a whole unit, while stale clusters sink intact. A node with no
    age (missing timestamp) contributes nothing, so a subtree of only undated
    nodes sorts last.
    """
    children: dict[IssueKey, list[IssueKey]] = {}
    roots: list[IssueKey] = []
    for key, row in index.items():
        parent_key = (
            (row.repo, row.parent_number) if row.parent_number is not None else None
        )
        if parent_key is not None and parent_key in index:
            children.setdefault(parent_key, []).append(key)
        else:
            roots.append(key)

    # Sentinel age for nodes/subtrees with no datable activity: sorts them last.
    NO_AGE = 10**9
    min_age_cache: dict[IssueKey, int] = {}

    def subtree_min_age(key: IssueKey) -> int:
        if key in min_age_cache:
            return min_age_cache[key]
        row = index[key]
        best = row.age_days if row.age_days is not None else NO_AGE
        for child in children.get(key, []):
            best = min(best, subtree_min_age(child))
        min_age_cache[key] = best
        return best

    def ordered(keys: list[IssueKey]) -> list[IssueKey]:
        return sorted(keys, key=lambda k: (subtree_min_age(k), k))

    def make(key: IssueKey, depth: int) -> TreeNode:
        kids = ordered(children.get(key, []))
        return TreeNode(
            row=index[key],
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


def group_by_repo(index: dict[IssueKey, IssueRow]) -> list[tuple[str, list[TreeNode]]]:
    """Partition ``index`` by repo, each repo built into its own forest.

    Groundwork for an owner-wide view (issue #43): nothing calls this from the
    CLI yet. Each repo's slice of the index is handed to :func:`build_forest`
    unchanged — the cross-repo guard already applied in :func:`build_index`
    means every tree here is self-contained, so partitioning first and
    building per-partition is safe and simple.

    Sections are ordered most-recently-active first: ascending by the minimum
    ``age_days`` across *all* of a repo's rows (mirroring the ``NO_AGE``
    sentinel :func:`build_forest` uses, so a repo with no datable activity at
    all sorts last), ties broken alphabetically by repo name.
    """
    by_repo: dict[str, dict[IssueKey, IssueRow]] = {}
    for key, row in index.items():
        by_repo.setdefault(row.repo, {})[key] = row

    NO_AGE = 10**9

    def repo_min_age(rows: dict[IssueKey, IssueRow]) -> int:
        ages = [row.age_days for row in rows.values() if row.age_days is not None]
        return min(ages) if ages else NO_AGE

    ordered_repos = sorted(
        by_repo, key=lambda name: (repo_min_age(by_repo[name]), name)
    )
    return [(name, build_forest(by_repo[name])) for name in ordered_repos]


# --- sprint view -------------------------------------------------------------
#
# The ``--sprint`` view consumes Projects v2 board items (see
# ``github.fetch_sprint_items``) rather than the issue forest. It is *not* a tree:
# items are grouped into three assignee buckets — yours, others, unassigned — each
# a flat list sorted active-first by board Status. ``status`` keeps the board's raw
# value (emoji and all); the terminal renderer strips the emoji, markdown keeps it.


@dataclass(frozen=True)
class SprintRow:
    """One current-sprint board item, normalized for the sprint view."""

    number: int
    url: str
    title: str
    labels: list[str]
    labels_hidden: int  # labels beyond the fetched page (the +N tail)
    comments_count: int
    age_days: int | None
    is_stale: bool
    sub_total: int
    sub_completed: int
    assignees: list[str]
    status: str | None
    is_mine: bool
    is_unassigned: bool
    pr_state: str | None = None
    pr_number: int | None = None

    @property
    def has_children(self) -> bool:
        return self.sub_total > 0


@dataclass
class SprintView:
    """The three assignee buckets of a sprint, plus the sprint's display name."""

    title: str | None
    yours: list[SprintRow]
    others: list[SprintRow]
    unassigned: list[SprintRow]

    @property
    def is_empty(self) -> bool:
        return not (self.yours or self.others or self.unassigned)


# Literal emoji, plus the variation-selector (U+FE0F) and ZWJ (U+200D) that
# decorate them. Board Status values often carry an emoji prefix (e.g. a board
# might name a column "🚀 Shipping"); emoji are double-width, which terminal
# column math (code-point counts) can't account for, so the terminal Status
# cell strips them via :func:`strip_emoji`. Markdown keeps them. ``status_rank``
# and ``github.validate_board_fields`` also fold through this (via
# :func:`normalize_status`) so a configured ``statusOrder`` entry matches what
# the user actually sees on screen, not the board's raw (possibly
# emoji-prefixed) name. Escapes are spelled out so the class stays legible.
_EMOJI_RE = re.compile("[\U0001f000-\U0001faff\U00002600-\U000027bf\ufe0f\u200d]+")


def strip_emoji(value: str) -> str:
    """Drop literal emoji (and their selectors) — for the terminal Status cell."""
    return _EMOJI_RE.sub("", value).strip()


def normalize_status(status: str) -> str:
    """Fold a status name for comparison: emoji-stripped, then case-folded.

    The one normalisation both :func:`status_rank` and
    ``github.validate_board_fields`` apply, so a ``statusOrder`` entry written
    as the user sees it on screen (``"Priority"``) matches a raw board name
    that carries an emoji (``"🚀 Priority"``) — on both sides of the ranking
    comparison, and in the up-front validation that checks configured entries
    against the board's real options.
    """
    return strip_emoji(status).casefold()


def status_rank(status: str | None, status_order: Sequence[str]) -> int:
    """Sort key for the active-first Status order: listed first, rest last.

    Compared emoji-stripped + case-folded on both sides (see
    :func:`normalize_status`), so a ``statusOrder`` entry configured as
    displayed ("Priority") matches a board Status that carries an emoji
    ("🚀 Priority").
    """
    if status is None:
        return len(status_order)
    normalized = normalize_status(status)
    order = [normalize_status(entry) for entry in status_order]
    return order.index(normalized) if normalized in order else len(status_order)


def _field_value(item: dict[str, Any], key: str, subkey: str) -> str | None:
    """Read a non-empty string from ``item[key][subkey]`` (a project fieldValue)."""
    value = item.get(key)
    if isinstance(value, dict):
        inner = compact_text(value.get(subkey))
        if inner:
            return inner
    return None


def build_sprint_view(
    items: list[dict[str, Any]],
    *,
    login: str,
    repo: str,
    now: datetime | None,
    stale_days: int,
    status_order: Sequence[str],
) -> SprintView:
    """Project board items -> a :class:`SprintView` of three sorted buckets.

    Non-issue items (PRs, drafts) and items belonging to a different repository
    than ``repo`` are dropped — the board can span repos. The sprint title is the
    iteration value of the first surviving item. Each bucket sorts active-first by
    ``status_order`` (ties by issue number, descending).
    """
    # The sprint title comes from any item's iteration value — read it before the
    # repo/issue filtering so an active-but-empty sprint (a real iteration with no
    # issues for this repo) still reports its name, letting the CLI tell that case
    # apart from "no active sprint at all".
    title: str | None = None
    for item in items:
        title = _field_value(item, "iteration", "title")
        if title is not None:
            break

    rows: list[SprintRow] = []
    for item in items:
        # Belt-and-braces (#2): under a correct ``@current`` filter every item
        # carries an iteration value, so one that doesn't wasn't really matched
        # by the sprint filter — drop it rather than render it as "this sprint".
        if _field_value(item, "iteration", "title") is None:
            continue
        content = item.get("content") or {}
        if content.get("__typename") != "Issue":
            continue
        repo_name = (content.get("repository") or {}).get("nameWithOwner")
        if repo_name != repo:
            continue
        assignees = _assignees(content)
        age = age_in_days(content.get("updatedAt"), now)
        sub_total, sub_completed = _sub_summary(content)
        pr_state, pr_number = dominant_pr(content)
        rows.append(
            SprintRow(
                number=int(content.get("number", 0)),
                url=str(content.get("url") or ""),
                title=compact_text(content.get("title")),
                labels=_labels(content),
                labels_hidden=_labels_hidden(content),
                comments_count=_comments_count(content),
                age_days=age,
                is_stale=age is not None and age >= stale_days,
                sub_total=sub_total,
                sub_completed=sub_completed,
                assignees=assignees,
                status=_field_value(item, "status", "name"),
                is_mine=login in assignees,
                is_unassigned=not assignees,
                pr_state=pr_state,
                pr_number=pr_number,
            )
        )

    def sort_key(row: SprintRow) -> tuple[int, int]:
        return (status_rank(row.status, status_order), -row.number)

    return SprintView(
        title=title,
        yours=sorted((r for r in rows if r.is_mine), key=sort_key),
        others=sorted(
            (r for r in rows if not r.is_mine and not r.is_unassigned), key=sort_key
        ),
        unassigned=sorted((r for r in rows if r.is_unassigned), key=sort_key),
    )
