"""I/O boundary: shelling out to ``git`` and ``gh``. The only impure module.

Everything here depends on the environment (a git repo, an authenticated ``gh``,
the network), so every failure is surfaced as :class:`IssueCheckError` for the
CLI to turn into a clean message + non-zero exit. No other module shells out.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

# One repo-wide query, filtered to the current user server-side. Sub-issue
# fields (``parent``, ``subIssuesSummary``) are GraphQL-only — they are not in
# the ``gh issue list --json`` REST field set — which is why this is GraphQL
# from day one. ``first: 100`` is the page cap; ``fetch_assigned_issues``
# paginates via ``endCursor`` when needed.
#
# The ``parent`` chain is fetched three levels deep so the tree view can place
# each owned issue under its (possibly un-owned) ancestors from this single
# query. Trees deeper than that lose their topmost ancestors — fine for the
# shallow epic→task hierarchies this targets.
#
# ``closedByPullRequestsReferences`` (with ``includeClosedPrs``) gives the
# "fix in flight" signal — the PRs linked to an issue, with state + draft flag.
# It is fetched only on owned issues, not on context ancestors, which carry no
# PR marker. ``includeClosedPrs: true`` deliberately returns closed/merged PRs
# too so the renderer can distinguish a live fix from a landed or dead one.
ISSUE_QUERY = """
fragment NodeFields on Issue {
  number
  title
  url
  updatedAt
  subIssuesSummary { total completed }
}
query($q: String!, $endCursor: String) {
  search(query: $q, type: ISSUE, first: 100, after: $endCursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        ...NodeFields
        labels(first: 10) { nodes { name } }
        comments { totalCount }
        closedByPullRequestsReferences(first: 10, includeClosedPrs: true) {
          nodes { number state isDraft }
        }
        parent {
          ...NodeFields
          parent {
            ...NodeFields
            parent { ...NodeFields }
          }
        }
      }
    }
  }
}
"""

_REMOTE_PATTERNS = [
    r"^git@github\.com:(?P<repo>[^/]+/[^/]+?)(?:\.git)?$",
    r"^https://github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$",
    r"^ssh://git@github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$",
]


class IssueCheckError(Exception):
    """A user-facing failure. The CLI prints the message to stderr and exits 1."""


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    binary = args[0]
    try:
        return subprocess.run(args, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        if binary == "gh":
            hint = (
                "Install it from https://cli.github.com and run 'gh auth login'."
            )
        else:
            hint = f"Install {binary} and ensure it is on PATH."
        raise IssueCheckError(f"'{binary}' is not installed. {hint}") from exc


def repo_from_remote(remote: str) -> str | None:
    """Parse ``OWNER/REPO`` from a git remote URL, falling back to ``gh``."""
    remote = remote.strip()
    for pattern in _REMOTE_PATTERNS:
        match = re.match(pattern, remote)
        if match:
            return match.group("repo")
    # Non-github host, insteadOf rewrites, etc. — let gh resolve it.
    result = _run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def current_repo() -> str:
    """``OWNER/REPO`` for the git repo containing the cwd.

    Raises :class:`IssueCheckError` with an actionable message when the cwd is
    not inside a git repository with a GitHub ``origin`` remote.
    """
    result = _run(["git", "remote", "get-url", "origin"])
    if result.returncode != 0:
        raise IssueCheckError(
            "Not inside a git repository with a GitHub 'origin' remote.\n"
            "Run issue-check from a cloned GitHub repo, or pass --repo OWNER/REPO."
        )
    repo = repo_from_remote(result.stdout)
    if repo is None:
        raise IssueCheckError(
            "Could not derive OWNER/REPO from the origin remote: "
            f"{result.stdout.strip()}\nPass --repo OWNER/REPO explicitly."
        )
    return repo


def current_login() -> str | None:
    """The authenticated GitHub login, or ``None`` if it can't be determined."""
    result = _run(["gh", "api", "user", "--jq", ".login"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def fetch_assigned_issues(
    repo: str, login: str, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """Open issues assigned to ``login`` in ``repo``, paginating as needed.

    Returns ``(issues, total_assigned)`` where ``total_assigned`` is the
    server's own count (used only for the truncation note).
    """
    query_str = f"repo:{repo} is:issue is:open assignee:{login}"
    issues: list[dict[str, Any]] = []
    total = 0
    cursor: str | None = None

    while True:
        args = [
            "gh", "api", "graphql",
            "-f", f"query={ISSUE_QUERY}",
            "-f", f"q={query_str}",
        ]
        if cursor:
            args += ["-f", f"endCursor={cursor}"]

        result = _run(args)
        if result.returncode != 0:
            raise IssueCheckError(
                f"gh failed to fetch issues for {repo}:\n{result.stderr.strip()}"
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise IssueCheckError(f"Could not parse gh response: {exc}") from exc
        if payload.get("errors"):
            raise IssueCheckError("GraphQL error: " + json.dumps(payload["errors"]))

        search = (payload.get("data") or {}).get("search") or {}
        total = search.get("issueCount", total)
        issues.extend(node for node in (search.get("nodes") or []) if node)

        if len(issues) >= limit:
            return issues[:limit], total
        page = search.get("pageInfo") or {}
        cursor = page.get("endCursor")
        if not page.get("hasNextPage") or not cursor:
            return issues, total


# Sprint view: one Projects v2 board, filtered server-side to its *current*
# iteration. The board's ``items(query:)`` argument takes the same filter syntax
# as the board search bar, and ``<field>:@current`` resolves the active iteration
# from its dates GitHub-side — so we fetch only the current sprint (a handful of
# items) in one page, with no client-side date math. ``content`` carries the full
# Issue (the same fields the yours-view uses) and ``fieldValueByName`` reads the
# board's Status + Iteration values. The board can span repos, so the model
# filters items to the requested repo; PR/draft items are dropped there too.
def _sprint_query(
    owner: str, owner_type: str, number: int, sprint_field: str, status_field: str
) -> str:
    root = "organization" if owner_type == "organization" else "user"
    # ``json.dumps`` yields a valid GraphQL string literal (quoted + escaped), so
    # an owner or field name containing a quote can't break out of the query. The
    # number is an int, safe to interpolate directly.
    owner_lit = json.dumps(owner)
    status_lit = json.dumps(status_field)
    sprint_lit = json.dumps(sprint_field)
    return f"""
query($q: String!, $endCursor: String) {{
  {root}(login: {owner_lit}) {{
    projectV2(number: {number}) {{
      items(first: 100, after: $endCursor, query: $q) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          content {{
            __typename
            ... on Issue {{
              number title url updatedAt state
              repository {{ nameWithOwner }}
              assignees(first: 10) {{ nodes {{ login }} }}
              labels(first: 10) {{ nodes {{ name }} }}
              comments {{ totalCount }}
              subIssuesSummary {{ total completed }}
              parent {{ number }}
              closedByPullRequestsReferences(first: 10, includeClosedPrs: true) {{
                nodes {{ number state isDraft }}
              }}
            }}
          }}
          status: fieldValueByName(name: {status_lit}) {{
            ... on ProjectV2ItemFieldSingleSelectValue {{ name }}
          }}
          iteration: fieldValueByName(name: {sprint_lit}) {{
            ... on ProjectV2ItemFieldIterationValue {{ title }}
          }}
        }}
      }}
    }}
  }}
}}
"""


def sprint_filter(sprint_field: str) -> str:
    """The board ``items(query:)`` token for the current sprint.

    The board filter language keys off the iteration field's name (lowercased);
    for GitHub's default ``Iteration`` field this is ``iteration:@current``.
    """
    token = sprint_field.strip().lower() or "iteration"
    return f"{token}:@current"


def fetch_sprint_items(
    owner: str,
    owner_type: str,
    number: int,
    sprint_field: str,
    status_field: str,
) -> list[dict[str, Any]]:
    """Current-sprint items of a Projects v2 board, paginating as needed."""
    query_str = _sprint_query(owner, owner_type, number, sprint_field, status_field)
    q = sprint_filter(sprint_field)
    root_key = "organization" if owner_type == "organization" else "user"
    items: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        args = [
            "gh", "api", "graphql",
            "-f", f"query={query_str}",
            "-f", f"q={q}",
        ]
        if cursor:
            args += ["-f", f"endCursor={cursor}"]

        result = _run(args)
        if result.returncode != 0:
            raise IssueCheckError(
                f"gh failed to fetch project {owner}/{number}:\n"
                f"{result.stderr.strip()}"
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise IssueCheckError(f"Could not parse gh response: {exc}") from exc
        if payload.get("errors"):
            raise IssueCheckError("GraphQL error: " + json.dumps(payload["errors"]))

        root = (payload.get("data") or {}).get(root_key) or {}
        project = root.get("projectV2")
        if project is None:
            raise IssueCheckError(
                f"No project #{number} found for {owner} "
                "(check the project URL and that `gh` has read:project scope)."
            )
        connection = project.get("items") or {}
        items.extend(node for node in (connection.get("nodes") or []) if node)

        page = connection.get("pageInfo") or {}
        cursor = page.get("endCursor")
        if not page.get("hasNextPage") or not cursor:
            return items
