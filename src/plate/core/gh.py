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

import re
import subprocess

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
