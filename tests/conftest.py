"""Session-wide safety net: no test may shell out.

The suite is offline and sub-second because every test stubs the boundary —
``plate.core.gh.run_command`` or a domain's ``fetch_*``. A path that slips
through would otherwise run a real ``gh``/``git``, so the process chokepoint is
replaced with something that fails loudly instead of quietly hitting the
network. A test that wants to simulate the boundary itself (a missing binary,
say) still overrides this within its own scope.
"""

from __future__ import annotations

from typing import Any, NoReturn

import pytest

from plate.core import gh


@pytest.fixture(autouse=True)
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError(
            "tests must not shell out — patch plate.core.gh.run_command"
        )

    monkeypatch.setattr(gh.subprocess, "run", refuse)
