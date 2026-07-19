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
PR_QUERY = """
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
        commits(last: 1) {
          nodes { commit { statusCheckRollup { state } } }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


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
    repo: str, limit: int
) -> tuple[str | None, list[dict[str, Any]]]:
    """One GraphQL round trip for the viewer login and all open PRs in ``repo``.

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
        f"query={PR_QUERY}",
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
