"""Session-wide safety nets: no shelling out, and no reading the real config.

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

import argparse
import pathlib
from collections.abc import Callable
from typing import Any, NoReturn

import pytest

from plate.core import config, gh


@pytest.fixture(autouse=True)
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``plate.core.gh.subprocess.run`` to raise instead of shelling out."""

    def refuse(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError(
            "tests must not shell out — patch plate.core.gh.run_command, "
            "or gh.subprocess.run in your own scope"
        )

    monkeypatch.setattr(gh.subprocess, "run", refuse)


@pytest.fixture(autouse=True)
def isolated_config_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point config resolution at an empty $HOME with no config env vars set.

    ``plate.cli.main`` loads the config for every view that declares
    ``--config``, so without this the suite would read whatever config the
    machine running it happens to have.
    """
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("PLATE_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture
def run_with_config() -> Callable[
    [Callable[[argparse.Namespace, config.Config], int], argparse.Namespace], int
]:
    """Dispatch as plate.cli.main does: load the config, then hand both over."""

    def dispatch(
        run: Callable[[argparse.Namespace, config.Config], int],
        args: argparse.Namespace,
    ) -> int:
        return run(args, config.load_config(args.config))

    return dispatch
