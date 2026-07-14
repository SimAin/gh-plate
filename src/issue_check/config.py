"""User configuration: which labels are "special", and how to treat them.

This is a deliberate, opt-in break from the otherwise label-agnostic design
(``DECISIONS.md`` D2): some labels (``blocked`` and friends) carry real signal,
so the user can name them here and have the renderer call them out. It stays in
the user's control and is easy to update — that is the whole point.

The one module that reads a config file. Kept apart from the GitHub I/O so the
model/render layers stay pure; failures surface as :class:`IssueCheckError`.

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

import fnmatch
import json
import os
from dataclasses import dataclass, field
from typing import Any

from .github import IssueCheckError

# Recognised styles. The renderer owns how each *looks*; here we only validate.
#   alert -> red, warn -> gold, info -> green  (all promoted to the cell front)
#   hide  -> drop the label from the cell entirely
LABEL_STYLES = ("alert", "warn", "info", "hide")

# Built-in defaults, merged *under* the user's file so the tool is useful before
# any config exists. Override or extend by writing the config file.
DEFAULT_LABEL_STYLES: dict[str, str] = {"blocked": "alert"}


@dataclass
class Config:
    label_styles: dict[str, str] = field(default_factory=dict)

    def style_for(self, label: str) -> str | None:
        """The style for ``label``: case-insensitive, with ``*`` glob support."""
        key = label.strip().lower()
        if key in self.label_styles:
            return self.label_styles[key]
        for pattern, style in self.label_styles.items():
            if "*" in pattern and fnmatch.fnmatch(key, pattern):
                return style
        return None


def config_path() -> str:
    """The resolved config location: ``$ISSUE_CHECK_CONFIG`` or the XDG default."""
    env = os.environ.get("ISSUE_CHECK_CONFIG")
    if env:
        return env
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "issue-check", "config.json")


def parse_config(data: Any) -> Config:
    """Validate a decoded-JSON object into a :class:`Config` (over the defaults)."""
    styles = dict(DEFAULT_LABEL_STYLES)
    if not isinstance(data, dict):
        raise IssueCheckError("Config root must be a JSON object.")
    labels = data.get("labels", {})
    if not isinstance(labels, dict):
        raise IssueCheckError('Config "labels" must be an object of name -> style.')
    for name, style in labels.items():
        if not isinstance(style, str) or style not in LABEL_STYLES:
            raise IssueCheckError(
                f"Unknown style {style!r} for label {name!r}. "
                f"Valid styles: {', '.join(LABEL_STYLES)}."
            )
        styles[str(name).strip().lower()] = style
    return Config(label_styles=styles)


def load_config(path: str | None = None) -> Config:
    """Load config from ``path`` (or the default location), or defaults if absent."""
    target = path or config_path()
    if not os.path.exists(target):
        return Config(label_styles=dict(DEFAULT_LABEL_STYLES))
    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise IssueCheckError(f"Could not read config at {target}: {exc}") from exc
    return parse_config(data)
