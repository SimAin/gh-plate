"""Machine-readable output: the ``--format json`` envelope every view shares.

A script consuming stdout needs what the terminal reader gets for free —
which view produced the payload, when, for whom, and the honesty notes the
other formats print to stderr — so the envelope carries all of it and the
notes never go to stderr in this format. Domains supply ``data``; nothing
here knows a domain's shape. ``schema`` is bumped only when an existing key
changes meaning or disappears; new keys are not a bump.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from typing import Any

from plate.core.text import format_timestamp

SCHEMA_VERSION = 1


def plain(value: Any) -> Any:
    """``value`` with dataclasses as dicts and tuples as lists, recursively —
    what :func:`json.dumps` can take without a custom encoder. Anything else
    passes through as is, so a model field must already be a JSON type
    (str/int/bool/None) — a ``datetime`` or ``set`` would fail at ``dumps``."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: plain(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def envelope(
    *,
    command: str,
    view: str,
    now: datetime,
    login: str | None,
    data: Any,
    notes: list[str],
    repo: str | None = None,
    owner: str | None = None,
    assignee: str | None = None,
    sprint: dict[str, Any] | None = None,
    stale_days: int | None = None,
) -> dict[str, Any]:
    """The envelope around one view's ``data``. Every key is always present;
    a scope that doesn't apply to the view is null."""
    return {
        "schema": SCHEMA_VERSION,
        "command": command,
        "view": view,
        "generated_at": format_timestamp(now),
        "repo": repo,
        "owner": owner,
        "login": login,
        "assignee": assignee,
        "sprint": sprint,
        "stale_days": stale_days,
        "notes": list(notes),
        "data": plain(data),
    }


def dumps(payload: dict[str, Any]) -> str:
    """The envelope as indented UTF-8 JSON (glyphs kept, not escaped)."""
    return json.dumps(payload, indent=2, ensure_ascii=False)
