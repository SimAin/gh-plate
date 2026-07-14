"""Tests for issue_check.cli — argument parsing."""

from __future__ import annotations

import pytest

from issue_check import __version__, cli


def test_version_flag_prints_version_and_exits(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(["--version"])
    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_limit_zero_is_rejected(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["--limit", "0"])
    assert excinfo.value.code == 2
    assert "positive integer" in capsys.readouterr().err


def test_limit_negative_is_rejected(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["--limit", "-5"])
    assert excinfo.value.code == 2
    assert "positive integer" in capsys.readouterr().err


def test_stale_days_zero_is_rejected(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["--stale-days", "0"])
    assert excinfo.value.code == 2
    assert "positive integer" in capsys.readouterr().err


def test_valid_limit_still_parses() -> None:
    args = cli.parse_args(["--limit", "10"])
    assert args.limit == 10


def test_valid_stale_days_still_parses() -> None:
    args = cli.parse_args(["--stale-days", "30"])
    assert args.stale_days == 30


def test_defaults_when_not_given() -> None:
    args = cli.parse_args([])
    assert args.limit == cli.DEFAULT_LIMIT
    assert args.stale_days == cli.DEFAULT_STALE_DAYS
