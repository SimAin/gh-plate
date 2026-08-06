"""PR-domain GitHub fetches: the repository pull-request GraphQL query and its
``gh api graphql --paginate`` multi-document parsing.

Builds on :mod:`plate.core.gh` for the shared ``gh``/``git`` plumbing
(:func:`~plate.core.gh.run_command`, :class:`~plate.core.gh.PlateError`) — this
module owns everything PR-specific: the query, pagination, and page merging.
No other prs-domain module shells out.
"""

from __future__ import annotations

import json
from typing import Any

from plate.core import gh

# One round trip for the viewer's login, the open PRs, GitHub's badge comment
# count (totalCommentsCount — issue comments + review summaries + inline
# review comments, the figure the UI shows), and GitHub's own status-check
# rollup state. reviewRequests and the author __typename are fetched for
# review-request and bot handling. `gh api graphql --paginate` walks pages via
# $endCursor when the limit needs more than one.
#
# The last-activity signal reads one trailing event per channel: the head
# commit, `reviews(last: 1)` — not latestOpinionatedReviews, so comment-only
# reviews count — and `comments(last: 1)`. createdAt anchors the Age column.
#
# The template's __EXTRA_FIELDS__ slot lets the --timeline variant add its
# per-node events connection; the plain query carries no trace of it.
_PR_QUERY_TEMPLATE = """
query($owner: String!, $name: String!, $pageSize: Int!, $endCursor: String) {
  viewer { login }
  repository(owner: $owner, name: $name) {
    pullRequests(
      states: OPEN
      first: $pageSize
      after: $endCursor
      orderBy: {field: CREATED_AT, direction: DESC}
    ) {
      nodes {
        number
        title
        url
        isDraft
        createdAt
        updatedAt
        mergeable
        totalCommentsCount
        reviewDecision
        author { login __typename }
        assignees(first: 20) { nodes { login } }
        latestReviews: latestOpinionatedReviews(first: 30) {
          nodes { state author { login } }
        }
        reviewRequests(first: 30) {
          nodes { requestedReviewer { ... on User { login } } }
        }
        reviews(last: 1) {
          nodes { submittedAt author { login __typename } }
        }
        comments(last: 1) {
          nodes { createdAt author { login __typename } }
        }
        commits(last: 1) {
          nodes {
            commit {
              committedDate
              author { user { login } }
              statusCheckRollup { state }
            }
          }
        }
__EXTRA_FIELDS__
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

# The --timeline strip needs event history, not just each channel's trailing
# item. 30 events cover a 28-day window for all but the chattiest PRs; older
# days beyond the fetched events simply render quiet.
TIMELINE_FIELDS = """\
        timelineItems(
          last: 30
          itemTypes: [PULL_REQUEST_COMMIT, PULL_REQUEST_REVIEW, ISSUE_COMMENT]
        ) {
          nodes {
            __typename
            ... on PullRequestCommit {
              commit { committedDate author { user { login } } }
            }
            ... on PullRequestReview {
              submittedAt state author { login __typename }
            }
            ... on IssueComment {
              createdAt author { login __typename }
            }
          }
        }"""

PR_QUERY = _PR_QUERY_TEMPLATE.replace("__EXTRA_FIELDS__\n", "")
PR_TIMELINE_QUERY = _PR_QUERY_TEMPLATE.replace("__EXTRA_FIELDS__", TIMELINE_FIELDS)


# The owner-wide view (issue #54) is a single owner-scoped search rather than a
# per-repo enumeration — GitHub's ``search(type: ISSUE)`` returns PRs too, so a
# ``is:pr`` search across ``org:``/``user:`` fetches every open PR an owner has
# in one paginated query. The ``... on PullRequest`` node carries exactly the
# fields the repo view's normaliser reads (so ``model.normalize_rows`` consumes
# both shapes unchanged), plus ``repository { nameWithOwner }`` — every node in
# an owner search can live in a different repo, and that field is what
# ``group_by_repo`` sections on. The viewer's login is *not* fetched here (a
# search has no ``viewer`` root); the CLI passes it from ``gh.current_login()``,
# mirroring the issues owner path.
PR_OWNER_QUERY = """
fragment PrFields on PullRequest {
  number
  title
  url
  isDraft
  createdAt
  updatedAt
  mergeable
  totalCommentsCount
  reviewDecision
  author { login __typename }
  assignees(first: 20) { nodes { login } }
  latestReviews: latestOpinionatedReviews(first: 30) {
    nodes { state author { login } }
  }
  reviewRequests(first: 30) {
    nodes { requestedReviewer { ... on User { login } } }
  }
  reviews(last: 1) {
    nodes { submittedAt author { login __typename } }
  }
  comments(last: 1) {
    nodes { createdAt author { login __typename } }
  }
  commits(last: 1) {
    nodes {
      commit {
        committedDate
        author { user { login } }
        statusCheckRollup { state }
      }
    }
  }
  repository { nameWithOwner }
}
query($q: String!, $pageSize: Int!, $endCursor: String) {
  search(query: $q, type: ISSUE, first: $pageSize, after: $endCursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        ...PrFields
      }
    }
  }
}
"""


def owner_search_query(
    owner: str, owner_type: str, login: str, *, mine: bool
) -> str:
    """The GitHub search string for every open PR across ``owner``.

    ``owner_type`` (``"organization"`` or ``"user"`` — the same vocabulary as
    ``config.ProjectConfig.owner_type``) picks the qualifier: an organization is
    searched with ``org:OWNER``, a user account with ``user:OWNER``. ``is:pr``
    scopes the shared ``search(type: ISSUE)`` connection to pull requests.

    When ``mine`` is set, ``author:LOGIN`` narrows to PRs *you authored* — see
    DECISIONS.md D9. This differs deliberately from the repo view's
    author-or-assignee "mine": qualifiers AND together, so author-or-assignee
    cannot be expressed as a single search term, and "my PRs across an owner"
    most naturally means the ones you opened.

    ``archived:false`` excludes archived repos by design (a done repo's PRs are
    not live work); ``sort:updated-desc`` makes the result deterministic and,
    under truncation, drops the *least* recently active PRs first — the
    active-first ethos everywhere else (D1, D6).
    """
    qualifier = "org" if owner_type == "organization" else "user"
    query_str = (
        f"{qualifier}:{owner} is:pr is:open archived:false sort:updated-desc"
    )
    if mine:
        query_str += f" author:{login}"
    return query_str


def fetch_owner_prs(
    owner: str, owner_type: str, login: str, limit: int, *, mine: bool
) -> tuple[list[dict[str, Any]], int]:
    """Open PRs across every repo ``owner`` has, paginating as needed.

    Builds the search string via :func:`owner_search_query` and delegates to
    :func:`plate.core.gh.search_paginated` (the shared cursor loop) bound to
    :data:`PR_OWNER_QUERY`. Returns ``(prs, total)`` where ``total`` is the
    server's own count; GitHub caps any search at 1000 results, so for a large
    owner ``total`` can exceed what pagination retrieves — the CLI compares
    ``len(prs) < total`` to report a partial result honestly.
    """
    query_str = owner_search_query(owner, owner_type, login, mine=mine)
    return gh.search_paginated(PR_OWNER_QUERY, query_str, limit, owner)


def parse_graphql_documents(text: str) -> list[dict[str, Any]]:
    """Split `gh api graphql --paginate` output into its JSON documents.

    --paginate concatenates one JSON document per page with no separator, so
    plain json.loads only works for a single page.
    """
    decoder = json.JSONDecoder()
    documents: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        document, index = decoder.raw_decode(text, index)
        if isinstance(document, dict):
            documents.append(document)
    return documents


def merge_graphql_pages(
    documents: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Combine paginated query documents into (viewer login, PR nodes)."""
    viewer: str | None = None
    prs: list[dict[str, Any]] = []
    for document in documents:
        data = document.get("data")
        if not isinstance(data, dict):
            continue
        viewer_data = data.get("viewer")
        if isinstance(viewer_data, dict):
            login = viewer_data.get("login")
            if isinstance(login, str) and login:
                viewer = login
        repository = data.get("repository")
        connection = (
            repository.get("pullRequests") if isinstance(repository, dict) else None
        )
        nodes = connection.get("nodes") if isinstance(connection, dict) else None
        if isinstance(nodes, list):
            prs.extend(node for node in nodes if isinstance(node, dict))
    return viewer, prs


# This deliberately keeps gh-pr-status's own `gh api graphql --paginate`
# pagination style rather than unifying with plate.issues.github's cursor
# loop (see `_search_issues`): the two query shapes differ (a repository
# connection here vs. a top-level search there), and the issue #62 port's job
# is parity with gh-pr-status, not unifying the two pagination strategies.
def fetch_prs_and_viewer(
    repo: str, limit: int, *, timeline: bool = False
) -> tuple[str | None, list[dict[str, Any]]]:
    """One GraphQL round trip for the viewer login and all open PRs in ``repo``.

    ``timeline`` opts into the heavier per-node events connection.
    Raises :class:`~plate.core.gh.PlateError` when ``repo`` isn't shaped like
    ``OWNER/REPO``, when ``gh`` fails, or when its output can't be parsed.
    """
    if "/" not in repo:
        raise gh.PlateError(f"Expected repository as OWNER/REPO, got: {repo}")
    owner, name = repo.split("/", 1)
    page_size = min(limit, 100)
    command = ["gh", "api", "graphql"]
    if limit > page_size:
        command.append("--paginate")
    command += [
        "-f",
        f"query={PR_TIMELINE_QUERY if timeline else PR_QUERY}",
        "-F",
        f"owner={owner}",
        "-F",
        f"name={name}",
        "-F",
        f"pageSize={page_size}",
    ]
    result = gh.run_command(command)
    if result.returncode != 0:
        raise gh.PlateError(
            f"gh failed to fetch open PRs for {repo}:\n{result.stderr.strip()}"
        )

    try:
        documents = parse_graphql_documents(result.stdout)
    except ValueError as exc:
        raise gh.PlateError(f"Could not parse gh GraphQL JSON: {exc}") from exc

    viewer, prs = merge_graphql_pages(documents)
    return viewer, prs[:limit]
