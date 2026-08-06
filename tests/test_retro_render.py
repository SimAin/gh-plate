"""Tests for plate.retro.render — the panel and its markdown counterpart."""

from __future__ import annotations

from datetime import UTC, datetime

from plate.core.render import (
    BOLD,
    DIM,
    RESET,
    SOFT_GOLD,
    visible_length,
)
from plate.retro import render
from plate.retro.model import RetroChannel

NOW = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)  # a Friday
DAYS = 14


def channel(
    label: str, counts: list[int], last_days: int | None
) -> RetroChannel:
    return RetroChannel(
        label=label, counts=counts, total=sum(counts), last_days=last_days
    )


def quiet(label: str) -> RetroChannel:
    return channel(label, [0] * DAYS, None)


def active(label: str, last_days: int = 0) -> RetroChannel:
    counts = [0] * DAYS
    counts[DAYS - 1 - last_days] = 2
    return channel(label, counts, last_days)


def three(reviews_last: int | None = 2) -> list[RetroChannel]:
    reviews = (
        quiet("reviews") if reviews_last is None else active("reviews", reviews_last)
    )
    return [reviews, active("pushes", 0), active("opened", 5)]


def test_panel_structure_and_alignment() -> None:
    out = render.panel(three(), DAYS, NOW, use_color=False)
    lines = out.splitlines()
    assert lines[0].startswith("── you · last 14 days ")
    ruler = lines[1]
    # Cells start after the label field; the ruler ends on today + Σ header.
    assert visible_length(ruler) == render.LABEL_WIDTH + DAYS * render.CELL + 4
    assert ruler.rstrip().endswith("Σ")
    assert " F " in ruler  # NOW is a Friday
    for line, label in zip(lines[2:], ("reviews", "pushes", "opened"), strict=True):
        assert line.startswith(f"   {label}")


def test_totals_column_right_aligned_after_the_cells() -> None:
    out = render.panel(three(), DAYS, NOW, use_color=False)
    commits_line = next(line for line in out.splitlines() if "pushes" in line)
    cells_end = render.LABEL_WIDTH + DAYS * render.CELL
    assert commits_line[cells_end : cells_end + 4] == "   2"


def test_quiet_days_are_dim_dots_and_today_is_bold() -> None:
    out = render.panel(three(), DAYS, NOW, use_color=True)
    assert f"{DIM} · {RESET}" in out
    assert f"{BOLD} 2 {RESET}" in out  # commits today
    assert f"{BOLD} F {RESET}" in out  # today's ruler letter


def test_weekend_ruler_letters_are_dim() -> None:
    out = render.panel(three(), DAYS, NOW, use_color=True)
    ruler = out.splitlines()[1]
    assert f"{DIM} S {RESET}" in ruler
    assert f"{DIM} F {RESET}" not in ruler  # weekdays stay plain


def test_reviews_nudge_gold_when_quiet_two_days() -> None:
    out = render.panel(three(reviews_last=2), DAYS, NOW, use_color=True)
    assert f"{SOFT_GOLD}last 2d ago{RESET}" in out


def test_reviews_nudge_gold_when_quiet_all_window() -> None:
    out = render.panel(three(reviews_last=None), DAYS, NOW, use_color=True)
    assert f"{SOFT_GOLD}none in 14d{RESET}" in out


def test_no_nudge_when_reviewed_recently_or_on_other_channels() -> None:
    out = render.panel(three(reviews_last=1), DAYS, NOW, use_color=True)
    assert SOFT_GOLD not in out
    # opened has been quiet 5 days — dim, never gold.
    assert f"{DIM}last 5d ago{RESET}" in out


def test_annotations_cover_the_edges() -> None:
    out = render.panel(three(), DAYS, NOW, use_color=False)
    assert "today" in out
    assert "last 2d ago" in out
    out = render.panel(three(reviews_last=None), DAYS, NOW, use_color=False)
    assert "none in 14d" in out


def test_cells_cap_at_two_digits_but_the_total_stays_honest() -> None:
    busy = channel("pushes", [0] * (DAYS - 1) + [123], 0)
    out = render.panel([busy], DAYS, NOW, use_color=False)
    row = next(line for line in out.splitlines() if "pushes" in line)
    cells = row[: render.LABEL_WIDTH + DAYS * render.CELL]
    assert "99" in cells
    assert "123" not in cells
    assert f"{123:>4}" in row  # Σ is 4 wide and uncapped


def test_color_never_has_no_ansi() -> None:
    assert "\033[" not in render.panel(three(), DAYS, NOW, use_color=False)


def test_markdown_table_totals_and_recency() -> None:
    out = render.markdown_table(three(), DAYS)
    assert "| Channel | Total | Last |" in out
    assert "| reviews | 2 | last 2d ago |" in out
    assert "| pushes | 2 | today |" in out
    assert "\033[" not in out


def test_markdown_none_in_window() -> None:
    out = render.markdown_table(three(reviews_last=None), DAYS)
    assert "| reviews | 0 | none in 14d |" in out
