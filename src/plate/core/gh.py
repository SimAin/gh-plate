"""Shared I/O boundary: shelling out to ``git`` and ``gh``. The only impure
module that plate's domain packages depend on for process/network access.

Everything here depends on the environment (a git repo, an authenticated ``gh``,
the network), so every failure is surfaced as :class:`PlateError` for the CLI to
turn into a clean message + non-zero exit. Domain packages (``plate.issues``,
and later a ``plate.prs``) build their own GraphQL/REST calls on top of
:func:`run_command`; repo, login, and owner-type resolution are common enough
across domains to live here once. No other module shells out.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any, NamedTuple

_REMOTE_PATTERNS = [
    r"^git@github\.com:(?P<repo>[^/]+/[^/]+?)(?:\.git)?$",
    r"^https://github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$",
    r"^ssh://git@github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$",
]


class PlateError(Exception):
    """A user-facing failure. The CLI prints the message to stderr and exits 1."""


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    binary = args[0]
    try:
        return subprocess.run(args, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        if binary == "gh":
            hint = "Install it from https://cli.github.com and run 'gh auth login'."
        else:
            hint = f"Install {binary} and ensure it is on PATH."
        raise PlateError(f"'{binary}' is not installed. {hint}") from exc


def repo_from_remote(remote: str) -> str | None:
    """Parse ``OWNER/REPO`` from a git remote URL, falling back to ``gh``."""
    remote = remote.strip()
    for pattern in _REMOTE_PATTERNS:
        match = re.match(pattern, remote)
        if match:
            return match.group("repo")
    # Non-github host, insteadOf rewrites, etc. — let gh resolve it.
    result = run_command(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def current_repo() -> str:
    """``OWNER/REPO`` for the git repo containing the cwd.

    Raises :class:`PlateError` with an actionable message when the cwd is
    not inside a git repository with a GitHub ``origin`` remote.
    """
    result = run_command(["git", "remote", "get-url", "origin"])
    if result.returncode != 0:
        raise PlateError(
            "Not inside a git repository with a GitHub 'origin' remote.\n"
            "Run plate from a cloned GitHub repo, or pass --repo OWNER/REPO."
        )
    repo = repo_from_remote(result.stdout)
    if repo is None:
        raise PlateError(
            "Could not derive OWNER/REPO from the origin remote: "
            f"{result.stdout.strip()}\nPass --repo OWNER/REPO explicitly."
        )
    return repo


def current_login() -> str | None:
    """The authenticated GitHub login, or ``None`` if it can't be determined."""
    result = run_command(["gh", "api", "user", "--jq", ".login"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def resolve_owner_type(owner: str) -> str:
    """Whether ``owner`` is a GitHub organization or a user account.

    One cheap ``gh api users/{owner}`` call, mapping the account's ``.type``
    (``"Organization"`` / ``"User"``) to the ``"organization"`` / ``"user"``
    vocabulary the rest of the module uses (see ``config.ProjectConfig``).

    This doubles as up-front validation: an owner that doesn't exist, or that
    ``gh`` can't see, fails fast here with an actionable message, instead of
    silently reaching :func:`plate.issues.github.fetch_owner_issues` and
    coming back with an empty result that looks like "no open issues" rather
    than "no such owner".
    """
    result = run_command(["gh", "api", f"users/{owner}", "--jq", ".type"])
    if result.returncode != 0:
        raise PlateError(
            f"GitHub owner '{owner}' not found or not accessible.\n"
            "Check the name (an organization or username), and that 'gh' is "
            "authenticated."
        )
    account_type = result.stdout.strip()
    if account_type == "Organization":
        return "organization"
    if account_type == "User":
        return "user"
    raise PlateError(
        f"GitHub owner '{owner}' has an unexpected account type "
        f"'{account_type}' (expected 'Organization' or 'User')."
    )


# GitHub answers an over-expensive search page with a bare HTTP 502 (sometimes
# 503/504): the query exceeded its server-side time budget. Per GitHub's own
# guidance, retry and request fewer nodes — each page gets ``_MAX_ATTEMPTS``
# tries, halving the page size on every transient 5xx (100 → 50 → 25).
_TRANSIENT_HTTP = re.compile(r"HTTP (50[234])")
_MAX_PAGE_SIZE = 100
_MIN_PAGE_SIZE = 25
_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0


# A self-overwriting stderr status line (``\r`` + erase-line), so a slow
# multi-page search — especially one sleeping through 502 retries — doesn't
# read as a hang. TTY-gated: pipes, redirects, and scripts see nothing.
def _progress(message: str) -> None:
    if not sys.stderr.isatty():
        return
    sys.stderr.write(f"\r\x1b[2K{message}")
    sys.stderr.flush()


def _progress_clear() -> None:
    if not sys.stderr.isatty():
        return
    sys.stderr.write("\r\x1b[2K")
    sys.stderr.flush()


class GhAttempt(NamedTuple):
    """What :func:`run_gh_with_retry` came back with."""

    result: subprocess.CompletedProcess[str]
    status: str  # the last transient HTTP status seen; "5xx" if there was none
    exhausted: bool  # every attempt failed transiently
    attempts: int  # how many calls were actually made


def run_gh_with_retry(
    build_args: Callable[[], list[str]],
    *,
    on_transient: Callable[[str, int], None] | None = None,
) -> GhAttempt:
    """Run a ``gh`` call, retrying only transient 5xx answers.

    The policy is shared — what counts as transient, the backoff, how many
    tries — but the messages are not: a search timing out and an activity feed
    failing need different advice, so callers inspect the returned attempt and
    word their own :class:`PlateError`. ``build_args`` is called once per try
    so a caller can shrink its request between them; ``on_transient`` fires on
    every transient failure, before any sleep.
    """
    status = "5xx"
    attempt = 1
    while True:
        result = run_command(build_args())
        if result.returncode == 0:
            return GhAttempt(result, status, False, attempt)
        transient = _TRANSIENT_HTTP.search(result.stderr)
        if not transient:
            return GhAttempt(result, status, False, attempt)
        status = transient.group(1)
        if on_transient is not None:
            on_transient(status, attempt)
        if attempt >= _MAX_ATTEMPTS:
            return GhAttempt(result, status, True, attempt)
        time.sleep(_RETRY_DELAY_SECONDS * attempt)
        attempt += 1


def search_paginated(
    query: str, query_str: str, limit: int, error_context: str
) -> tuple[list[dict[str, Any]], int]:
    """:func:`search_paginated_with_viewer` minus the viewer login — for
    callers whose document requests no ``viewer`` root, or that resolve the
    login elsewhere. Same contract otherwise.
    """
    nodes, total, _viewer = search_paginated_with_viewer(
        query, query_str, limit, error_context
    )
    return nodes, total


def search_paginated_with_viewer(
    query: str, query_str: str, limit: int, error_context: str
) -> tuple[list[dict[str, Any]], int, str | None]:
    """Run a GraphQL ``search`` document against ``query_str``, paginating.

    The shared engine behind every owner/repo-scoped GitHub search across
    domains: the issue owner/assigned views and the PR owner view are all the
    same top-level ``search(type: ISSUE, first: $pageSize, after: $endCursor)``
    connection under a caller-supplied GraphQL document (``query``), differing
    only in the node fields each requests and the qualifiers built into
    ``query_str``. Keeping the cursor loop here — rather than once per domain —
    is the D8-legitimate infra move: no view behaviour depends on it.

    ``query`` must declare ``$pageSize: Int!`` and feed it to the search's
    ``first:`` — pages are sized to what ``limit`` still needs and shrink
    when GitHub times a page out (see ``_TRANSIENT_HTTP`` above).

    ``error_context`` is interpolated into the failure message (a repo or an
    owner name) so each caller's error stays specific to what it was fetching.

    The document may also declare ``viewer { login }`` as a root field (a
    GraphQL document can combine it with ``search``): the authenticated login
    then rides along on every page at zero extra latency, letting callers
    filter with ``@me`` yet still learn the concrete login without a separate
    ``gh api user`` round trip. It is taken from whichever page carries it and
    returned third — ``None`` when the document never requested it.

    Returns ``(nodes, total, viewer)`` where ``total`` is the server's own
    ``issueCount`` (used only for the truncation note). GitHub caps any single
    search at 1000 results, so for a large owner ``total`` can exceed what
    pagination ever delivers — it is not only the true count clipped by
    ``limit``.

    A transient stderr status line (see ``_progress``) shows fetch and retry
    progress; the ``finally`` clears it on every exit, return or raise.
    """
    nodes: list[dict[str, Any]] = []
    total = 0
    viewer: str | None = None
    cursor: str | None = None
    page_cap = _MAX_PAGE_SIZE

    def page_args() -> list[str]:
        fetched = f" {len(nodes)}/{min(total, limit)}" if nodes else ""
        _progress(f"Fetching from GitHub for {error_context}…{fetched}")
        page_size = min(page_cap, limit - len(nodes))
        args = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            f"q={query_str}",
            "-F",
            f"pageSize={page_size}",
        ]
        if cursor:
            args += ["-f", f"endCursor={cursor}"]
        return args

    def shrink_page(status: str, attempt: int) -> None:
        nonlocal page_cap
        page_cap = max(page_cap // 2, _MIN_PAGE_SIZE)
        if attempt < _MAX_ATTEMPTS:
            _progress(
                f"GitHub timed out (HTTP {status}) — retrying with "
                f"page size {page_cap} "
                f"(attempt {attempt + 1}/{_MAX_ATTEMPTS})…"
            )

    try:
        while True:
            attempt = run_gh_with_retry(page_args, on_transient=shrink_page)
            if attempt.exhausted:
                raise PlateError(
                    f"gh search failed for {error_context}: GitHub answered "
                    f"HTTP {attempt.status} on {_MAX_ATTEMPTS} attempts "
                    f"(page size reduced to {page_cap}).\n"
                    "That status is GitHub timing the search out server-side "
                    "— it happens intermittently on large owner-wide "
                    "searches. Wait a moment and rerun; if it persists, try "
                    "a lower --limit."
                )
            result = attempt.result
            if result.returncode != 0:
                raise PlateError(
                    f"gh search failed for {error_context}:\n{result.stderr.strip()}"
                )
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise PlateError(f"Could not parse gh response: {exc}") from exc
            if payload.get("errors"):
                raise PlateError("GraphQL error: " + json.dumps(payload["errors"]))

            data = payload.get("data") or {}
            viewer_node = data.get("viewer")
            if isinstance(viewer_node, dict):
                login = viewer_node.get("login")
                if isinstance(login, str) and login:
                    viewer = login
            search = data.get("search") or {}
            total = search.get("issueCount", total)
            nodes.extend(node for node in (search.get("nodes") or []) if node)

            if len(nodes) >= limit:
                return nodes[:limit], total, viewer
            page = search.get("pageInfo") or {}
            cursor = page.get("endCursor")
            if not page.get("hasNextPage") or not cursor:
                return nodes, total, viewer
    finally:
        _progress_clear()
