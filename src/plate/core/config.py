"""User configuration: which labels are "special", and how to treat them.

This is a deliberate, opt-in break from the otherwise label-agnostic design
(``DECISIONS.md`` D2): some labels (``blocked`` and friends) carry real signal,
so the user can name them here and have the renderer call them out. It stays in
the user's control and is easy to update — that is the whole point.

The one module that reads a config file. Kept apart from the GitHub I/O so the
model/render layers stay pure; failures surface as :class:`PlateError`.

Config is JSON (stdlib-parseable on the 3.11 floor, no new dependency) mapping a
label name to a *style*. Matching is case-insensitive and supports ``*`` globs
for prefixed schemes (``status:*``)::

    {
      "labels": {
        "blocked":   "alert",
        "needs-info": "warn",
        "wontfix":    "hide"
      }
    }
"""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from .gh import PlateError

# Recognised styles. The renderer owns how each *looks*; here we only validate.
#   alert -> red, warn -> gold, info -> green  (all promoted to the cell front)
#   hide  -> drop the label from the cell entirely
LABEL_STYLES = ("alert", "warn", "info", "hide")

# Built-in defaults, merged *under* the user's file so the tool is useful before
# any config exists. Override or extend by writing the config file.
DEFAULT_LABEL_STYLES: dict[str, str] = {"blocked": "alert"}

# GitHub's own default field names for a Projects v2 board. Used when a repo's
# ``project`` entry doesn't name the sprint/status fields explicitly.
DEFAULT_SPRINT_FIELD = "Iteration"
DEFAULT_STATUS_FIELD = "Status"

# Project references: a board URL (org or user), or shorthand ``OWNER/projects/N``.
_PROJECT_URL_RE = re.compile(
    r"^(?:https?://github\.com/)?(?P<kind>orgs|users)/"
    r"(?P<owner>[^/]+)/projects/(?P<num>\d+)"
)
_PROJECT_SHORT_RE = re.compile(r"^(?P<owner>[^/]+)/projects/(?P<num>\d+)$")

# Recognised keys per level. Unknown keys are warned about, not rejected, so a
# config written for a newer plate still loads; a hand-edit typo just loses that
# key silently otherwise (#33). Comparison is exact-case: real keys are
# camelCase, so lowercase typos get flagged and a suggestion offered.
_KNOWN_CONFIG_KEYS = ("labels", "repos", "owners")
_KNOWN_REPO_KEYS = ("project", "sprintField", "statusField", "statusOrder")


def _warn_unknown_keys(present: Any, known: tuple[str, ...], where: str = "") -> None:
    """Warn on stderr for each key in ``present`` not in ``known`` (non-fatal)."""
    if not isinstance(present, dict):
        return
    location = f" in {where}" if where else ""
    for key in present:
        if key in known:
            continue
        match = difflib.get_close_matches(str(key), known, n=1)
        hint = f' (did you mean "{match[0]}"?)' if match else ""
        print(
            f'plate: warning: unrecognised config key "{key}"{location}{hint}',
            file=sys.stderr,
        )


@dataclass(frozen=True)
class ProjectConfig:
    """Where a repo's sprint board lives, and which fields drive the view.

    ``owner_type`` is ``"organization"`` or ``"user"`` — the GraphQL root used to
    reach the project. ``status_order`` lists statuses front-to-back for the
    active-first sort; anything unlisted sorts last (see ``model.status_rank``).
    """

    owner: str
    owner_type: str
    number: int
    sprint_field: str = DEFAULT_SPRINT_FIELD
    status_field: str = DEFAULT_STATUS_FIELD
    status_order: tuple[str, ...] = ()


def parse_project_url(value: str) -> tuple[str, str, int]:
    """Parse a project reference into ``(owner, owner_type, number)``.

    Accepts ``https://github.com/orgs/OWNER/projects/N`` (and the ``users/``
    form), with any trailing ``/views/M`` etc., plus shorthand
    ``OWNER/projects/N`` (assumed an org). Raises :class:`PlateError`.
    """
    text = value.strip()
    match = _PROJECT_URL_RE.match(text)
    if match:
        owner_type = "organization" if match.group("kind") == "orgs" else "user"
        return match.group("owner"), owner_type, int(match.group("num"))
    match = _PROJECT_SHORT_RE.match(text)
    if match:
        return match.group("owner"), "organization", int(match.group("num"))
    raise PlateError(
        f"Could not parse project reference {value!r}. Expected a URL like "
        "https://github.com/orgs/OWNER/projects/N (or .../users/OWNER/projects/N), "
        "or shorthand OWNER/projects/N."
    )


@dataclass
class Config:
    label_styles: dict[str, str] = field(default_factory=dict)
    projects: dict[str, ProjectConfig] = field(default_factory=dict)
    # Issue #43 originally sketched this block as "organizations", but the
    # flag it feeds was generalized to `--owner` (it must also cover repos
    # owned directly by a personal account, not just orgs), so the config
    # block — and this field — is named "owners". Keys are aliases, stored
    # stripped + lowercased for case-insensitive lookup; values are the owner
    # name (an org or username) exactly as the user wrote it.
    owners: dict[str, str] = field(default_factory=dict)

    def style_for(self, label: str) -> str | None:
        """The style for ``label``: case-insensitive, with ``*`` glob support."""
        key = label.strip().lower()
        if key in self.label_styles:
            return self.label_styles[key]
        for pattern, style in self.label_styles.items():
            if "*" in pattern and fnmatch.fnmatch(key, pattern):
                return style
        return None

    def project_for(self, repo: str) -> ProjectConfig | None:
        """The sprint-board config for ``OWNER/REPO``, or ``None`` if unmapped.

        Matching is case-insensitive: GitHub treats ``OWNER/REPO`` case-
        insensitively, and the casing of a git remote is arbitrary.
        """
        return self.projects.get(str(repo).strip().lower())

    def resolve_owner(self, name: str) -> str:
        """Resolve ``name`` through the ``owners`` alias table.

        Lookup is case-insensitive (``name`` is stripped + lowercased, the
        same normalization the alias keys get on load). On a hit, the mapped
        owner name is returned; on a miss, ``name`` is returned unchanged, so
        an unconfigured literal org or username keeps working with zero
        config.

        Collision rule: if an alias happens to equal a real owner's name,
        the alias wins and shadows the literal. This is deliberate and
        deterministic — the user explicitly configured that word to mean
        something else, so honour it; if that's not wanted, remove the
        alias to get the literal back.
        """
        key = name.strip().lower()
        return self.owners.get(key, name)


def config_path() -> str:
    """The config path that would be read, absent an explicit ``--config``.

    ``$PLATE_CONFIG`` if set, else ``~/.config/plate/config.json`` (honouring
    ``$XDG_CONFIG_HOME`` if set). Pure — no filesystem probing.
    """
    env = os.environ.get("PLATE_CONFIG")
    if env:
        return env
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser(
        "~/.config"
    )
    return os.path.join(xdg_config_home, "plate", "config.json")


def _parse_repo_settings(repo: Any, settings: Any) -> ProjectConfig:
    """Validate one ``repos`` entry into a :class:`ProjectConfig`."""
    if not isinstance(settings, dict):
        raise PlateError(f"Config for repo {repo!r} must be an object.")
    _warn_unknown_keys(settings, _KNOWN_REPO_KEYS, where=f"repo {repo!r}")
    project = settings.get("project")
    if not isinstance(project, str) or not project.strip():
        raise PlateError(
            f'Repo {repo!r} needs a "project" reference '
            "(e.g. https://github.com/orgs/OWNER/projects/N)."
        )
    owner, owner_type, number = parse_project_url(project)
    sprint_field = settings.get("sprintField", DEFAULT_SPRINT_FIELD)
    status_field = settings.get("statusField", DEFAULT_STATUS_FIELD)
    if not isinstance(sprint_field, str) or not isinstance(status_field, str):
        raise PlateError(
            f'Repo {repo!r}: "sprintField" and "statusField" must be strings.'
        )
    raw_order = settings.get("statusOrder", [])
    if not isinstance(raw_order, list) or not all(
        isinstance(item, str) for item in raw_order
    ):
        raise PlateError(
            f'Repo {repo!r}: "statusOrder" must be a list of status names.'
        )
    return ProjectConfig(
        owner=owner,
        owner_type=owner_type,
        number=number,
        sprint_field=sprint_field,
        status_field=status_field,
        status_order=tuple(raw_order),
    )


def parse_config(data: Any) -> Config:
    """Validate a decoded-JSON object into a :class:`Config` (over the defaults)."""
    styles = dict(DEFAULT_LABEL_STYLES)
    if not isinstance(data, dict):
        raise PlateError("Config root must be a JSON object.")
    _warn_unknown_keys(data, _KNOWN_CONFIG_KEYS)
    labels = data.get("labels", {})
    if not isinstance(labels, dict):
        raise PlateError('Config "labels" must be an object of name -> style.')
    for name, style in labels.items():
        if not isinstance(style, str) or style not in LABEL_STYLES:
            raise PlateError(
                f"Unknown style {style!r} for label {name!r}. "
                f"Valid styles: {', '.join(LABEL_STYLES)}."
            )
        styles[str(name).strip().lower()] = style

    projects: dict[str, ProjectConfig] = {}
    repos = data.get("repos", {})
    if not isinstance(repos, dict):
        raise PlateError(
            'Config "repos" must be an object of OWNER/REPO -> settings.'
        )
    for repo, settings in repos.items():
        projects[str(repo).strip().lower()] = _parse_repo_settings(repo, settings)

    owners: dict[str, str] = {}
    raw_owners = data.get("owners", {})
    if not isinstance(raw_owners, dict):
        raise PlateError(
            'Config "owners" must be an object of alias -> owner name.'
        )
    for alias, owner in raw_owners.items():
        if not isinstance(alias, str) or not alias.strip():
            raise PlateError(
                'Config "owners" must be an object of alias -> owner name '
                f"(got a blank alias for {owner!r})."
            )
        if not isinstance(owner, str) or not owner.strip():
            raise PlateError(
                'Config "owners" must be an object of alias -> owner name '
                f"(alias {alias!r} needs a non-empty owner name)."
            )
        owners[alias.strip().lower()] = owner

    return Config(label_styles=styles, projects=projects, owners=owners)


def load_config(path: str | None = None) -> Config:
    """Load config from ``path`` (or the default location), or defaults if absent."""
    target = path or config_path()
    if not os.path.exists(target):
        return Config(label_styles=dict(DEFAULT_LABEL_STYLES), owners={})
    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PlateError(f"Could not read config at {target}: {exc}") from exc
    return parse_config(data)
