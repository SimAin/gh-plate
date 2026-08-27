"""Session-wide safety net: refuse the ``gh`` chokepoint's ``subprocess.run``.

The suite is offline and sub-second because every test stubs the boundary —
``plate.core.gh.run_command`` or a domain's ``fetch_*``. This guard only
patches ``plate.core.gh.subprocess.run``; a path that slips through would
otherwise run a real ``gh``, so that call is replaced with something that
fails loudly instead of quietly hitting the network. ``Popen`` is not
covered — one test uses a real ``Popen`` deliberately for a broken-pipe
check. A test that wants to simulate the boundary itself still overrides
this within its own scope.
"""

from __future__ import annotations

from typing import Any, NoReturn

import pytest

from plate.core import gh


@pytest.fixture(autouse=True)
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``plate.core.gh.subprocess.run`` to raise instead of shelling out."""

    def refuse(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError(
            "tests must not shell out — patch plate.core.gh.run_command, "
            "or gh.subprocess.run in your own scope"
        )

    monkeypatch.setattr(gh.subprocess, "run", refuse)
