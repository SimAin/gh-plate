"""Tests for plate.core.text's timestamp round trip."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from plate.core.text import format_timestamp, parse_timestamp


def test_format_timestamp_normalises_to_utc_with_a_z_suffix() -> None:
    plus_two = timezone(timedelta(hours=2))
    local = datetime(2026, 6, 19, 14, 0, tzinfo=plus_two)
    assert format_timestamp(local) == "2026-06-19T12:00:00Z"
    assert format_timestamp(datetime(2026, 6, 19, 12, 0, tzinfo=UTC)) == (
        "2026-06-19T12:00:00Z"
    )


def test_format_timestamp_round_trips_through_parse() -> None:
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert parse_timestamp(format_timestamp(moment)) == moment
