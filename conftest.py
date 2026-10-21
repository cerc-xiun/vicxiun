"""Project-level pytest configuration.

Simulates the CI environment where ffmpeg and node are not on PATH
(autouse). Tests that exercise business logic install their own
passing Prereq-check stub via the CLI seam; this fixture just makes
sure the stub really is needed by removing ffmpeg/node from PATH for
every test, mirroring the GitHub Actions runner.

The Prereq behaviour has its own tests under ``tests/test_prereqs.py``
that explicitly bypass this autouse fixture by setting ``shutil.which``
back to its real behaviour.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _ci_like_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """Pretend ffmpeg and node are not on PATH for every test.

    Also redirects the settings file into a temporary directory so no
    test reads or writes the developer's real settings (ticket #16).
    """
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setenv("PATH", "")
    settings_dir = tmp_path_factory.mktemp("settings")
    from vicxiun import cli

    monkeypatch.setattr(cli, "settings_path", lambda: settings_dir / "settings.json")
    yield


@pytest.fixture
def with_prereqs(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Restore real ``shutil.which`` for tests that need to probe PATH.

    Used by ``tests/test_prereqs.py`` to confirm the Prereq check sees
    the machine state, not the stub.
    """
    monkeypatch.undo()  # undo the autouse shutil.which and PATH clear
    yield
