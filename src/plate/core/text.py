"""Data cleaning shared by every domain's model layer: untrusted or serialised
strings turned into usable values.

Not presentation — these run before anything is laid out, on the way in from a
``gh`` payload. ``compact_text`` is re-exported from :mod:`plate.core.render`
so its long-standing import path keeps working.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any

# Well-formed escape sequences: CSI (ESC [ … final), OSC (ESC ] … ST), and the
# two-byte ESC+Fe forms. Removed whole so no `[31m`-style residue is left behind.
_ESCAPE_SEQ_RE = re.compile(
    r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"  # CSI, 7- or 8-bit introducer
    r"|\x1b\][^\x1b\x07]*(?:\x1b\\|\x07)"  # OSC … ST
    r"|\x1b[@-Z\\-_]"  # two-byte ESC Fe
)


def compact_text(value: object) -> str:
    """One clean line from untrusted text (titles, labels, board fields).

    Escape sequences are removed and any remaining control characters (C0,
    DEL, C1) become spaces, so a crafted title can't smuggle terminal escapes
    or OSC-8 links into the output; whitespace then collapses."""
    if not isinstance(value, str):
        return ""
    stripped = _ESCAPE_SEQ_RE.sub("", value)
    plain = "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in stripped)
    return " ".join(plain.split())


def parse_timestamp(value: Any) -> datetime | None:
    """A GitHub ISO-8601 timestamp as an aware datetime, or None if unusable.

    Anything non-string, empty, or unparseable returns None rather than
    raising: a missing or malformed field is a rendering gap, not an error.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
