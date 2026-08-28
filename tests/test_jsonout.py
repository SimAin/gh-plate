"""Tests for plate.core.jsonout — the ``--format json`` envelope."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from plate.core import jsonout

NOW = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Leaf:
    name: str
    count: int | None = None


@dataclass
class Branch:
    leaf: Leaf
    leaves: list[Leaf] = field(default_factory=list)
    pair: tuple[str, int] = ("a", 1)


def test_plain_flattens_dataclasses_lists_tuples_and_dicts() -> None:
    branch = Branch(leaf=Leaf("x"), leaves=[Leaf("y", 2)], pair=("b", 2))
    assert jsonout.plain({"k": branch, "t": (1, 2)}) == {
        "k": {
            "leaf": {"name": "x", "count": None},
            "leaves": [{"name": "y", "count": 2}],
            "pair": ["b", 2],
        },
        "t": [1, 2],
    }
    assert jsonout.plain(Leaf) is Leaf  # a class is a value, not an instance
    assert jsonout.plain("ünïcode") == "ünïcode"


def test_envelope_carries_every_key_with_nulls_for_absent_scopes() -> None:
    payload = jsonout.envelope(
        command="prs", view="repo", now=NOW, login="user", data={}, notes=[]
    )
    assert list(payload) == [
        "schema",
        "command",
        "view",
        "generated_at",
        "repo",
        "owner",
        "login",
        "assignee",
        "sprint",
        "stale_days",
        "notes",
        "data",
    ]
    assert payload["schema"] == jsonout.SCHEMA_VERSION == 1
    assert payload["generated_at"] == "2026-06-19T12:00:00Z"
    assert payload["repo"] is None and payload["sprint"] is None
    assert payload["notes"] == []


def test_envelope_plains_its_data_and_copies_notes() -> None:
    notes = ["Note: one"]
    payload = jsonout.envelope(
        command="issues",
        view="assigned",
        now=NOW,
        login="user",
        repo="acme/widget",
        assignee="user",
        stale_days=14,
        notes=notes,
        data={"issues": [Leaf("x")]},
    )
    notes.append("mutated after")
    assert payload["notes"] == ["Note: one"]
    assert payload["data"] == {"issues": [{"name": "x", "count": None}]}
    assert payload["assignee"] == "user" and payload["stale_days"] == 14


def test_dumps_is_indented_utf8_json() -> None:
    text = jsonout.dumps({"title": "naïve — ok", "n": 1})
    assert json.loads(text) == {"title": "naïve — ok", "n": 1}
    assert "naïve — ok" in text  # glyphs kept, not \u-escaped
    assert text.startswith("{\n  ")
