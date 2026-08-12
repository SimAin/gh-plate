"""Tests for plate.retro.render — the per-owner panels and their markdown
counterpart."""

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
from plate.retro.model import RetroChannel, RetroSection

NOW = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)  # a Friday
DAYS = 14


def channel(label: str, counts: list[int], last_days: int | None) -> RetroChannel:
    return RetroChannel(
        label=label, counts=counts, total=sum(counts), last_days=last_days
    )


def quiet(label: str) -> RetroChannel:
    return channel(label, [0] * DAYS, None)


def active(label: str, last_days: int = 0) -> RetroChannel:
    counts = [0] * DAYS
    counts[DAYS - 1 - last_days] = 2
    return channel(label, counts, last_days)


def section(owner: str = "acme", reviews_last: int | None = 2) -> RetroSection:
    reviews = (
        quiet("reviews") if reviews_last is None else active("reviews", reviews_last)
    )
    channels = [
        reviews,
        active("commits", 0),
        active("opened", 5),
        quiet("closed"),
    ]
    return RetroSection(
        owner=owner,
        channels=channels,
        total=sum(c.total for c in channels),
    )


def test_panel_structure_and_alignment() -> None:
    out = render.panel([section()], DAYS, NOW, use_color=False)
    lines = out.splitlines()
    assert lines[0].startswith("── acme · last 14 days ")
    ruler = lines[1]
    # Cells start after the label field; the ruler ends on today + Σ header.
    assert visible_length(ruler) == render.LABEL_WIDTH + DAYS * render.CELL + 4
    assert ruler.rstrip().endswith("Σ")
    assert " F " in ruler  # NOW is a Friday
    labels = ("reviews", "commits", "opened", "closed")
    for line, label in zip(lines[2:], labels, strict=True):
        assert line.startswith(f"   {label}")


def test_one_self_contained_panel_per_owner_in_order() -> None:
    out = render.panel(
        [section("acme"), section("SimAin")], DAYS, NOW, use_color=False
    )
    assert out.index("── acme") < out.index("── SimAin")
    assert out.count("reviews") == 2  # each section carries its own rows
    rulers = [line for line in out.splitlines() if line.rstrip().endswith("Σ")]
    assert len(rulers) == 2


def test_owner_dividers_share_one_width() -> None:
    out = render.panel(
        [section("a-very-long-org-name"), section("io")], DAYS, NOW, use_color=False
    )
    dividers = [line for line in out.splitlines() if line.startswith("── ")]
    assert len({visible_length(line) for line in dividers}) == 1


def test_totals_column_right_aligned_after_the_cells() -> None:
    out = render.panel([section()], DAYS, NOW, use_color=False)
    commits_line = next(line for line in out.splitlines() if "commits" in line)
    cells_end = render.LABEL_WIDTH + DAYS * render.CELL
    assert commits_line[cells_end : cells_end + 4] == "   2"


def test_quiet_days_are_dim_dots_and_today_is_bold() -> None:
    out = render.panel([section()], DAYS, NOW, use_color=True)
    assert f"{DIM} · {RESET}" in out
    assert f"{BOLD} 2 {RESET}" in out  # commits today
    assert f"{BOLD} F {RESET}" in out  # today's ruler letter


def test_weekend_ruler_letters_are_dim() -> None:
    out = render.panel([section()], DAYS, NOW, use_color=True)
    ruler = out.splitlines()[1]
    assert f"{DIM} S {RESET}" in ruler
    assert f"{DIM} F {RESET}" not in ruler  # weekdays stay plain


def test_reviews_nudge_gold_when_quiet_two_days() -> None:
    out = render.panel([section(reviews_last=2)], DAYS, NOW, use_color=True)
    assert f"{SOFT_GOLD}last 2d ago{RESET}" in out


def test_reviews_nudge_gold_when_quiet_all_window() -> None:
    out = render.panel([section(reviews_last=None)], DAYS, NOW, use_color=True)
    assert f"{SOFT_GOLD}none in 14d{RESET}" in out


def test_no_nudge_when_reviewed_recently_or_on_other_channels() -> None:
    out = render.panel([section(reviews_last=1)], DAYS, NOW, use_color=True)
    assert SOFT_GOLD not in out
    # opened quiet 5 days, closed quiet all window — dim, never gold.
    assert f"{DIM}last 5d ago{RESET}" in out
    assert f"{DIM}none in 14d{RESET}" in out


def test_annotations_cover_the_edges() -> None:
    out = render.panel([section()], DAYS, NOW, use_color=False)
    assert "today" in out
    assert "last 2d ago" in out
    out = render.panel([section(reviews_last=None)], DAYS, NOW, use_color=False)
    assert "none in 14d" in out


def test_cells_cap_at_two_digits_but_the_total_stays_honest() -> None:
    busy = channel("commits", [0] * (DAYS - 1) + [123], 0)
    loud = RetroSection(owner="acme", channels=[busy], total=busy.total)
    out = render.panel([loud], DAYS, NOW, use_color=False)
    row = next(line for line in out.splitlines() if "commits" in line)
    cells = row[: render.LABEL_WIDTH + DAYS * render.CELL]
    assert "99" in cells
    assert "123" not in cells
    assert f"{123:>4}" in row  # Σ is 4 wide and uncapped


def test_color_never_has_no_ansi() -> None:
    assert "\033[" not in render.panel([section()], DAYS, NOW, use_color=False)


def test_markdown_one_heading_and_table_per_owner() -> None:
    out = render.markdown_table([section("acme"), section("SimAin")], DAYS)
    assert "## acme" in out
    assert "## SimAin" in out
    assert out.index("## acme") < out.index("## SimAin")
    assert out.count("| Channel | Total | Last |") == 2
    assert "| reviews | 2 | last 2d ago |" in out
    assert "| commits | 2 | today |" in out
    assert "| closed | 0 | none in 14d |" in out
    assert "\033[" not in out


def test_markdown_none_in_window() -> None:
    out = render.markdown_table([section(reviews_last=None)], DAYS)
    assert "| reviews | 0 | none in 14d |" in out
