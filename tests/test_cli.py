"""Tests for issue_check.cli — argument parsing."""

from __future__ import annotations

import pytest

from issue_check import __version__
from issue_check.cli import parse_args


def test_version_flag_prints_version_and_exits(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--version"])
    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out
