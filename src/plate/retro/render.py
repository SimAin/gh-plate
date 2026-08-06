"""Presentation: retro channels -> the terminal panel / markdown table.

Pure rendering. The panel: a weekday ruler (weekends dim, today bold), one
row per channel with digits for active days and dim dots for quiet ones, a
Σ totals column, and a per-row "when did I last…?" annotation. Colour stays
rationed: the one tint is the gold nudge on a reviews row that has been
quiet for two days or more — the glance the view exists for.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plate.core.render import DIM as DIM
from plate.core.render import SOFT_GOLD
from plate.core.render import bold as bold
from plate.core.render import colorize as colorize
from plate.core.render import dim as dim
from plate.core.render import divider as divider
from plate.core.render import format_age as format_age
from plate.core.render import visible_length as visible_length

from .model import RetroChannel

LABEL_WIDTH = 14  # three-space indent + channel label, cells start here
CELL = 3  # one day per cell: two digit columns + a space

_WEEKDAY_LETTERS = "MTWTFSS"
NUDGE_QUIET_DAYS = 2


def _annotation(channel: RetroChannel, days: int) -> str:
    if channel.last_days is None:
        return f"none in {days}d"
    if channel.last_days == 0:
        return "today"
    return f"last {format_age(channel.last_days)} ago"


def _annotation_color(channel: RetroChannel) -> str:
    # The motivating glance: reviews gone quiet get the nudge; a quiet
    # commits or opened row is nobody's duty.
    quiet = channel.last_days is None or channel.last_days >= NUDGE_QUIET_DAYS
    if channel.label == "reviews" and quiet:
        return SOFT_GOLD
    return DIM


def _cell(count: int, today: bool, use_color: bool) -> str:
    text = f"{min(count, 99):>2} " if count > 0 else " · "
    if today:
        return bold(text, use_color)
    if count <= 0:
        return dim(text, use_color)
    return text


def _ruler(days: int, now: datetime, use_color: bool) -> str:
    today = now.astimezone(UTC).date()
    cells = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        text = f" {_WEEKDAY_LETTERS[day.weekday()]} "
        if offset == 0:
            cells.append(bold(text, use_color))
        elif day.weekday() >= 5:
            cells.append(dim(text, use_color))
        else:
            cells.append(text)
    return " " * LABEL_WIDTH + "".join(cells) + dim(f"{'Σ':>4}", use_color)


def _row(channel: RetroChannel, days: int, use_color: bool) -> str:
    cells = "".join(
        _cell(count, index == days - 1, use_color)
        for index, count in enumerate(channel.counts)
    )
    label = "   " + dim(channel.label, use_color)
    pad = " " * (LABEL_WIDTH - 3 - len(channel.label))
    annotation = colorize(
        _annotation(channel, days), _annotation_color(channel), use_color
    )
    return f"{label}{pad}{cells}{channel.total:>4}   {annotation}"


def panel(
    channels: list[RetroChannel], days: int, now: datetime, use_color: bool
) -> str:
    """The full panel: divider, weekday ruler, one row per channel."""
    lines = [_ruler(days, now, use_color)] + [
        _row(channel, days, use_color) for channel in channels
    ]
    width = max(visible_length(line) for line in lines)
    header = divider(f"you · last {days} days", width, use_color)
    return "\n".join([header, *lines])


def markdown_table(channels: list[RetroChannel], days: int) -> str:
    """The colour-free variant: totals and recency only — the day grid does
    not survive markdown."""
    lines = ["| Channel | Total | Last |", "| --- | --- | --- |"]
    for channel in channels:
        lines.append(
            f"| {channel.label} | {channel.total} | {_annotation(channel, days)} |"
        )
    return "\n".join(lines)
