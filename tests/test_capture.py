"""Integration tests for capturing a live stream (issue #9).

Issue #9 is the main use case of the project: a live link in, a
playable MKV Capture from the start out. The CLI is the project's
only integration seam: tests substitute the metadata resolver and the
executor (which is what would talk to yt-dlp) so no test reaches the
network.

The contract under test:

* A live link is classified as ``CaptureKind`` and the per-Item line
  on stderr says so before the executor is invoked.
* The real run writes an MKV file at the resolved target path --
  the container is the entire safety net for an interrupted Capture
  (ADR-0007).
* The executor receives the Capture Plan with ``live_from_start`` and
  ``hls_use_mpegts`` set, so a real yt-dlp call would Capture from the
  earliest point the platform still holds and use the fragment format
  that lets MKV survive an interruption.
* A finished-video link is still handled as a Download -- Capture
  flags are not applied, the file is still MP4 (ticket #8's
  acceptance: "A link that is not live is still handled as a Download,
  unchanged" -- the issue makes that explicit).
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import pytest
from _fakes import (
    BASIC_DOWNLOAD,
    BASIC_LIVE,
    install_executor,
    install_resolver,
)

from vicxiun.cli import main
from vicxiun.engine import _build_executor_options
from vicxiun.plan import (
    CaptureKind,
    DownloadKind,
    profile_from_name,
    resolve_plan,
)

# ---------------------------------------------------------------------------
# Acceptance: a live link is classified as a Capture, and the plan says so
# ---------------------------------------------------------------------------


def test_resolve_plan_classifies_live_as_capture(tmp_path: Path) -> None:
    """``is_live=True`` from the engine becomes a Capture Plan.

    The CLI test below exercises this through ``main``; this unit test
    pins the same contract at the planning seam so the wiring is not
    the only thing keeping the classifier honest.
    """
    captured: list[str] = []

    def resolver(url: str) -> dict[str, object]:
        captured.append(url)
        return dict(BASIC_LIVE)

    plan = resolve_plan(
        "https://example.com/live",
        profile=profile_from_name("capture"),
        output_root=tmp_path,
        resolver=resolver,
    )

    assert captured == ["https://example.com/live"]
    assert isinstance(plan.kind, CaptureKind)
    assert plan.kind.is_live is True
    # The container chosen for a Capture is MKV (ADR-0007).
    assert plan.target_path.suffix == ".mkv"


def test_resolve_plan_classifies_finished_as_download(tmp_path: Path) -> None:
    """``is_live=False`` still becomes a Download Plan -- unchanged."""

    def resolver(url: str) -> dict[str, object]:
        return dict(BASIC_DOWNLOAD)

    plan = resolve_plan(
        "https://example.com/fin",
        profile=profile_from_name("phone-safe"),
        output_root=tmp_path,
        resolver=resolver,
    )

    assert isinstance(plan.kind, DownloadKind)
    assert plan.kind.is_live is False
    assert plan.target_path.suffix == ".mp4"


# ---------------------------------------------------------------------------
# Acceptance: real run writes an MKV file at the resolved target
# ---------------------------------------------------------------------------


def test_real_run_writes_mkv_for_a_live_link(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A live link writes an MKV file at the Uploader's folder.

    The acceptance criterion "The output is MKV, so that an interrupted
    Capture remains playable" lives at this seam: a real yt-dlp call
    writes the Capture, ADR-0007 pins the container, and the file we
    write at the resolved target is the one the player will open.
    """
    install_resolver(monkeypatch, {"https://example.com/live": BASIC_LIVE})

    def fake_executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"FAKE MKV BYTES")
        return plan.target_path

    install_executor(monkeypatch, fake_executor)

    code = main(["--folder", str(tmp_path), "https://example.com/live"])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    expected_dir = tmp_path / "Some Channel"
    assert expected_dir.is_dir()
    files = list(expected_dir.iterdir())
    assert len(files) == 1
    name = files[0].name
    # The filename carries the [id] suffix and the .mkv extension.
    assert "Tonight's Stream" in name
    assert "live999" in name
    assert name.endswith(".mkv")
    # The per-Item line on stderr identifies the kind as Capture so
    # the user sees what is happening before any bytes hit disk.
    assert "Capture" in captured.err
    assert "1 ok, 0 failed" in captured.err


def test_real_run_says_capture_before_invoking_the_executor(
    tmp_path: Path, monkeypatch
) -> None:
    """The per-Item ``Capture with profile X`` line is emitted before
    the executor is called.

    The acceptance criterion "the plan says so before work starts"
    is a *temporal* property: the user must see the plan first, then
    the Capture. We pin the ordering: the snapshot of stderr at the
    moment the executor fires already has the kind line.
    """
    install_resolver(monkeypatch, {"https://example.com/live": BASIC_LIVE})

    stderr_during_executor = {"lines": ""}
    fake_stderr = StringIO()

    def fake_executor(plan, progress):
        stderr_during_executor["lines"] = fake_stderr.getvalue()
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        return plan.target_path

    install_executor(monkeypatch, fake_executor)

    real_stderr = sys.stderr
    sys.stderr = fake_stderr
    try:
        main(["--folder", str(tmp_path), "https://example.com/live"])
    finally:
        sys.stderr = real_stderr

    # The kind-naming line is already on stderr by the time the
    # executor is called.
    assert "Capture" in stderr_during_executor["lines"]


# ---------------------------------------------------------------------------
# Acceptance: Capture receives live_from_start and hls_use_mpegts
# ---------------------------------------------------------------------------


def test_real_run_passes_live_from_start_to_executor(
    tmp_path: Path, monkeypatch
) -> None:
    """The executor sees ``live_from_start=True`` so yt-dlp Captures from
    the earliest point the platform still holds -- the main acceptance
    criterion of issue #9.
    """
    install_resolver(monkeypatch, {"https://example.com/live": BASIC_LIVE})

    seen_options: list[dict[str, object]] = []

    def fake_executor(plan, progress):
        # Re-derive the options the engine would have used for this plan.
        seen_options.append(_build_executor_options(plan, progress))
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        return plan.target_path

    install_executor(monkeypatch, fake_executor)

    code = main(["--folder", str(tmp_path), "https://example.com/live"])
    assert code == 0
    assert seen_options, "executor was not invoked"
    opts = seen_options[0]
    assert opts.get("live_from_start") is True
    # The other half of the safety story (ADR-0007): MKV needs each
    # fragment wrapped in MPEG-TS so the container can keep appending.
    assert opts.get("hls_use_mpegts") is True


def test_real_run_does_not_pass_capture_flags_for_finished_video(
    tmp_path: Path, monkeypatch
) -> None:
    """A finished-video link still has no Capture flags -- unchanged.

    The last acceptance criterion of #9: a link that is not live is
    still handled as a Download, unchanged.
    """
    install_resolver(monkeypatch, {"https://example.com/fin": BASIC_DOWNLOAD})

    seen_options: list[dict[str, object]] = []

    def fake_executor(plan, progress):
        seen_options.append(_build_executor_options(plan, progress))
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        return plan.target_path

    install_executor(monkeypatch, fake_executor)

    code = main(["--folder", str(tmp_path), "https://example.com/fin"])
    assert code == 0
    assert seen_options, "executor was not invoked"
    opts = seen_options[0]
    for key in ("live_from_start", "hls_use_mpegts"):
        assert key not in opts
    # The Download still picks up the phone-safe MP4 container.
    target_path = next((tmp_path / "Some Channel").iterdir())
    assert target_path.suffix == ".mp4"


# ---------------------------------------------------------------------------
# Acceptance: a mixed Job treats each Item by its kind
# ---------------------------------------------------------------------------


def test_real_run_routes_downloads_and_captures_to_their_own_paths(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One Download and one Capture in the same Job land at the right
    files with the right container. A user can hand the tool a session
    of mixed Items; each gets the treatment its kind warrants.
    """
    install_resolver(
        monkeypatch,
        {
            "https://example.com/fin": BASIC_DOWNLOAD,
            "https://example.com/live": BASIC_LIVE,
        },
    )

    def fake_executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        return plan.target_path

    install_executor(monkeypatch, fake_executor)

    code = main(
        [
            "--folder",
            str(tmp_path),
            "https://example.com/fin",
            "https://example.com/live",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err

    uploader_dir = tmp_path / "Some Channel"
    files = sorted(p.name for p in uploader_dir.iterdir())
    # MP4 for the finished video, MKV for the live stream.
    assert any(f.endswith(".mp4") for f in files)
    assert any(f.endswith(".mkv") for f in files)
    # Both per-Item lines are on stderr; the per-kind wording is the
    # acceptance criterion "the plan says so before work starts".
    assert "Download" in captured.err
    assert "Capture" in captured.err
