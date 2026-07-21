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
from typing import Any

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
            hint = (
                "Install it from https://cli.github.com and run 'gh auth login'."
            )
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


# GitHub's GraphQL API answers an over-expensive search with a bare HTTP 502
# (occasionally 503/504) rather than a structured error: the query exceeded
# its server-side time budget. Owner-wide searches hit this intermittently —
# 100 nodes per page, each fanning out into nested connections (reviews,
# review requests, status-check rollups), is sometimes more than GitHub will
# compute in time under load. GitHub's own guidance is to retry and to request
# fewer nodes per page, so ``search_paginated`` gives each page
# ``_MAX_ATTEMPTS`` tries, halving the page size on every transient failure
# (100 → 50 → 25) and keeping it shrunk for the rest of the run.
_TRANSIENT_HTTP = re.compile(r"HTTP (50[234])")
_MAX_PAGE_SIZE = 100
_MIN_PAGE_SIZE = 25
_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0


# A multi-page search — and especially one riding out 502 retries with their
# sleeps — can take long enough that a silent terminal reads as a hang. These
# paint a single self-overwriting status line on stderr: ``\r`` returns to the
# line start and ``ESC[2K`` erases it, so each update replaces the last and
# ``_progress_clear`` leaves no trace before the real output renders. Gated on
# stderr being a TTY so pipes, redirects, and scripts see nothing (the ANSI
# escape never reaches a non-terminal either).
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


def search_paginated(
    query: str, query_str: str, limit: int, error_context: str
) -> tuple[list[dict[str, Any]], int]:
    """Run a GraphQL ``search`` document against ``query_str``, paginating.

    The shared engine behind every owner/repo-scoped GitHub search across
    domains: the issue owner/assigned views and the PR owner view are all the
    same top-level ``search(type: ISSUE, first: $pageSize, after: $endCursor)``
    connection under a caller-supplied GraphQL document (``query``), differing
    only in the node fields each requests and the qualifiers built into
    ``query_str``. Keeping the cursor loop here — rather than once per domain —
    is the D8-legitimate infra move: no view behaviour depends on it.

    ``query`` must declare ``$pageSize: Int!`` and feed it to the search's
    ``first:`` — this loop sizes each page to what is still needed under
    ``limit`` (capped at GitHub's 100) and shrinks it when GitHub times a page
    out (see ``_TRANSIENT_HTTP`` above). A transient HTTP 5xx gets retried
    with a short pause; any other failure — and 5xx exhaustion — raises
    :class:`PlateError`.

    ``error_context`` is interpolated into the failure message (a repo or an
    owner name) so each caller's error stays specific to what it was fetching.

    Returns ``(nodes, total)`` where ``total`` is the server's own
    ``issueCount`` (used only for the truncation note). GitHub caps any single
    search at 1000 results, so for a large owner ``total`` can exceed what
    pagination ever delivers — it is not only the true count clipped by
    ``limit``.

    While fetching, a transient status line is painted on stderr (TTY only —
    see ``_progress``): which context is being fetched, running progress once
    a page has landed, and a note when a GitHub timeout forces a retry, since
    the retry sleeps are otherwise indistinguishable from a hang. The
    ``finally`` clears it on every exit — return or raise — so it never
    contaminates the real output.
    """
    nodes: list[dict[str, Any]] = []
    total = 0
    cursor: str | None = None
    page_cap = _MAX_PAGE_SIZE

    try:
        while True:
            status = "5xx"
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                fetched = f" {len(nodes)}/{min(total, limit)}" if nodes else ""
                _progress(f"Fetching from GitHub for {error_context}…{fetched}")
                page_size = min(page_cap, limit - len(nodes))
                args = [
                    "gh", "api", "graphql",
                    "-f", f"query={query}",
                    "-f", f"q={query_str}",
                    "-F", f"pageSize={page_size}",
                ]
                if cursor:
                    args += ["-f", f"endCursor={cursor}"]

                result = run_command(args)
                if result.returncode == 0:
                    break
                transient = _TRANSIENT_HTTP.search(result.stderr)
                if not transient:
                    raise PlateError(
                        f"gh search failed for {error_context}:\n"
                        f"{result.stderr.strip()}"
                    )
                status = transient.group(1)
                page_cap = max(page_cap // 2, _MIN_PAGE_SIZE)
                if attempt < _MAX_ATTEMPTS:
                    _progress(
                        f"GitHub timed out (HTTP {status}) — retrying with "
                        f"page size {page_cap} "
                        f"(attempt {attempt + 1}/{_MAX_ATTEMPTS})…"
                    )
                    time.sleep(_RETRY_DELAY_SECONDS * attempt)
            else:
                raise PlateError(
                    f"gh search failed for {error_context}: GitHub answered "
                    f"HTTP {status} on {_MAX_ATTEMPTS} attempts "
                    f"(page size reduced to {page_cap}).\n"
                    "That status is GitHub timing the search out server-side "
                    "— it happens intermittently on large owner-wide "
                    "searches. Wait a moment and rerun; if it persists, try "
                    "a lower --limit."
                )
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise PlateError(f"Could not parse gh response: {exc}") from exc
            if payload.get("errors"):
                raise PlateError(
                    "GraphQL error: " + json.dumps(payload["errors"])
                )

            search = (payload.get("data") or {}).get("search") or {}
            total = search.get("issueCount", total)
            nodes.extend(node for node in (search.get("nodes") or []) if node)

            if len(nodes) >= limit:
                return nodes[:limit], total
            page = search.get("pageInfo") or {}
            cursor = page.get("endCursor")
            if not page.get("hasNextPage") or not cursor:
                return nodes, total
    finally:
        _progress_clear()
