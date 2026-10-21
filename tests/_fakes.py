"""Shared test fixtures for vicxiun's CLI integration tests.

The metadata dicts and the resolver/executor installers are reused by
``test_execute.py`` (Downloads) and ``test_capture.py`` (Captures).
They live here so a regression in the contract is fixed in one place.

Every installer also pins a passing Prereq check stub at the CLI
seam. The project conftest.py autouse fixture removes ffmpeg/node
from PATH for every test, mirroring the CI runner; without the stub
the real Prereq check (correctly) refuses to run and the business-
logic tests never reach the executor seam. The Prereq behaviour has
its own dedicated tests under ``tests/test_prereqs.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from vicxiun import cli as cli_mod
from vicxiun.errors import UserError
from vicxiun.plan import Plan

# A representative finished-video metadata dict. Drives both the
# Download integration tests and the Capture "is_live=False unchanged"
# tests.
BASIC_DOWNLOAD: dict[str, object] = {
    "title": "Best of 2024",
    "uploader": "Some Channel",
    "ext": "mp4",
    "is_live": False,
    "format_description": "MP4, H.264 + AAC, up to 1080p",
    "video_id": "abc123XYZ",
}

# A representative live-stream metadata dict. Drives the Capture
# integration tests.
BASIC_LIVE: dict[str, object] = {
    "title": "Tonight's Stream",
    "uploader": "Some Channel",
    "ext": "mkv",
    "is_live": True,
    "format_description": ("MKV, bestvideo+bestaudio recorded until stopped"),
    "video_id": "live999",
}

# A representative playlist metadata dict (ticket #15): what the flat
# resolve of a many-video link returns. No per-video fields -- the
# count is what the large-playlist confirmation works from.
BASIC_PLAYLIST: dict[str, object] = {
    "_type": "playlist",
    "title": "A Big Mix",
    "uploader": "Some Channel",
    "id": "pl123",
    "playlist_count": 60,
}

# The executor's callable type. Module-level so both test files agree.
ExecutorFn = Callable[[Plan, Callable[[dict[str, object]], None] | None], Path]


def install_resolver(
    monkeypatch,
    mapping: dict[str, dict[str, object]],
) -> None:
    """Install a fake resolver plus a passing Prereq check stub.

    The stub lets the test exercise business logic without requiring
    ffmpeg / node on the test machine's PATH. The real Prereq check
    is tested separately under ``tests/test_prereqs.py``.
    """
    _install_passing_prereq(monkeypatch)

    def resolver(url: str) -> dict[str, object]:
        if url not in mapping:
            raise UserError(f"engine cannot resolve {url}")
        return dict(mapping[url])

    monkeypatch.setattr(cli_mod, "_resolver", resolver)


def install_executor(monkeypatch, fn: ExecutorFn) -> None:
    """Install a fake executor at the CLI seam."""
    monkeypatch.setattr(cli_mod, "_executor", fn)


def install_confirm(monkeypatch, fn: Callable[[int], bool]) -> None:
    """Install a fake large-playlist confirmation at the CLI seam.

    ``fn`` receives the count of Items found and answers whether the
    run may start (ticket #15).
    """
    monkeypatch.setattr(cli_mod, "_confirm", fn)


def _install_passing_prereq(monkeypatch) -> None:
    """Pin a no-op Prereq check so tests do not depend on ffmpeg/node."""
    monkeypatch.setattr(cli_mod, "_prereq_check", lambda: 0)
