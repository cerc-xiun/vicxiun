"""Disk-space protection: pre-flight floor and running floor (issue #12).

Free space is protected twice:

* Before a Capture starts, the target volume must hold the pre-flight
  floor (2 GB); below it the Capture does not start and the Item fails
  with an explanation of what is required and what is available. A
  Download is not subject to this floor.
* While any Item is written, free space is watched through the
  progress hook; below the running floor (500 MB) the transfer stops
  the same way a deliberate stop does -- the KeyboardInterrupt path --
  so a Capture keeps a playable recording and a Download is cleaned of
  partial artifacts, and the Queue stops with later Items named as not
  run.

The seam is the CLI exactly as elsewhere in the suite, plus the
``vicxiun.diskspace`` free-space probe and the monotonic clock (same
pattern as the wait tests: never real-time in tests). No test reaches
the network and no test measures the developer's real drive.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from _fakes import BASIC_DOWNLOAD, BASIC_LIVE, install_executor, install_resolver

from vicxiun import diskspace
from vicxiun.cli import main

GB = 1024 * 1024 * 1024
MB = 1024 * 1024


def _free_space(value: int | None):
    """Install a fake free-space probe reporting ``value`` bytes.

    ``None`` fakes an unmeasurable volume (``OSError``).
    """

    def install(monkeypatch) -> None:
        if value is None:

            def probe(target: Path) -> int:
                raise OSError("cannot measure the volume (test fake)")

        else:

            def probe(target: Path) -> int:
                return value

        monkeypatch.setattr(diskspace, "free_bytes", probe)

    return install


def _free_space_sequence(*values: int):
    """Install a probe reporting each value once, then the last forever.

    The running-floor Capture tests need this: the disk had room at the
    pre-flight check and fell below the running floor during recording.
    """

    def install(monkeypatch) -> None:
        remaining = list(values)

        def probe(target: Path) -> int:
            if len(remaining) > 1:
                return remaining.pop(0)
            return remaining[0]

        monkeypatch.setattr(diskspace, "free_bytes", probe)

    return install


# ---------------------------------------------------------------------------
# The floors themselves: single, documented constants (AC)
# ---------------------------------------------------------------------------


def test_the_floors_are_the_documented_constants() -> None:
    """The two floors and the interval are the module's whole tuning
    surface: pinned here so a silent change cannot hollow out the
    protection the ticket promises.
    """
    assert diskspace.PRE_FLIGHT_FLOOR_BYTES == 2 * GB
    assert diskspace.RUNNING_FLOOR_BYTES == 500 * MB
    assert diskspace.CHECK_INTERVAL_SECONDS == 5.0


def test_free_bytes_walks_up_to_the_nearest_existing_ancestor(
    tmp_path: Path,
) -> None:
    """The target's folder may not exist yet (a first run creates the
    output folders); the probe answers for the volume all the same.
    """
    deep = tmp_path / "no" / "such" / "folder" / "file.mkv"
    assert diskspace.free_bytes(deep) == shutil.disk_usage(tmp_path).free


# ---------------------------------------------------------------------------
# AC: the running check is periodic -- frequent enough, cheap enough
# ---------------------------------------------------------------------------


def test_the_running_check_remeasures_at_most_once_per_interval(
    monkeypatch,
) -> None:
    """One measure per interval, no more: the hook is called on every
    yt-dlp progress event, but the volume query is throttled, and the
    next hook in the chain still sees every event.
    """
    measures: list[int] = []
    clock = {"now": 100.0}
    monkeypatch.setattr(
        diskspace,
        "free_bytes",
        lambda target: measures.append(1) or 600 * MB,
    )
    monkeypatch.setattr(diskspace.time, "monotonic", lambda: clock["now"])

    followed: list[dict[str, object]] = []
    hook = diskspace.SpaceWatch(Path("anywhere"), 500 * MB, follow=followed.append)

    for _ in range(10):
        hook({"status": "downloading"})
    assert len(measures) == 1  # throttled within one interval
    assert len(followed) == 10  # the next hook saw every event

    clock["now"] = 100.0 + diskspace.CHECK_INTERVAL_SECONDS
    hook({"status": "downloading"})
    assert len(measures) == 2  # re-measured at the next interval


def test_an_unmeasurable_volume_never_stops_a_transfer(monkeypatch) -> None:
    """A guard that cannot read the drive must not fail the run."""

    def probe(target: Path) -> int:
        raise OSError("cannot measure the volume (test fake)")

    monkeypatch.setattr(diskspace, "free_bytes", probe)
    followed: list[dict[str, object]] = []
    hook = diskspace.SpaceWatch(Path("anywhere"), follow=followed.append)
    hook({"status": "downloading"})  # no raise
    assert len(followed) == 1


# ---------------------------------------------------------------------------
# AC: below the pre-flight floor, a Capture does not start
# ---------------------------------------------------------------------------


def test_a_capture_below_the_preflight_floor_does_not_start(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A nearly-full device is never handed a recording: the executor
    is never reached, and the refusal names what is required and what
    is available, and what to do next.
    """
    install_resolver(monkeypatch, {"https://example.com/live": BASIC_LIVE})
    _free_space(int(1.5 * GB))(monkeypatch)

    ran: list[str] = []

    def executor(plan, progress):
        ran.append(plan.url)
        return plan.target_path

    install_executor(monkeypatch, executor)

    code = main(["--folder", str(tmp_path), "https://example.com/live"])

    captured = capsys.readouterr()
    assert code == 2  # a named user error, not an interrupt
    assert ran == []  # the Capture did not start
    assert (tmp_path / "Some Channel").exists() is False
    err = captured.err
    assert "not enough free space to record" in err
    assert "2.0 GB" in err  # the floor: what is required
    assert "1.5 GB" in err  # what is available
    assert "Free up space" in err  # what to do next


def test_a_download_is_not_subject_to_the_preflight_floor(
    tmp_path: Path, monkeypatch
) -> None:
    """A Download on a device below the pre-flight floor is still
    attempted: the pre-flight floor is Capture-only; the running
    check is a Download's only protection.
    """
    install_resolver(monkeypatch, {"https://example.com/fin": BASIC_DOWNLOAD})
    _free_space(1 * GB)(monkeypatch)  # below 2 GB, above 500 MB

    ran: list[str] = []

    def executor(plan, progress):
        ran.append(plan.url)
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"FINISHED FILE")
        return plan.target_path

    install_executor(monkeypatch, executor)

    code = main(["--folder", str(tmp_path), "https://example.com/fin"])

    assert code == 0
    assert ran == ["https://example.com/fin"]


def test_a_preflight_refusal_does_not_stop_the_queue(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal is a per-Item failure like any other: the Queue
    continues past it, so a Download that still fits is handled.
    """
    install_resolver(
        monkeypatch,
        {
            "https://example.com/live": BASIC_LIVE,
            "https://example.com/fin": BASIC_DOWNLOAD,
        },
    )
    _free_space(int(1.5 * GB))(monkeypatch)

    def executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"FINISHED FILE")
        return plan.target_path

    install_executor(monkeypatch, executor)

    code = main(
        [
            "--folder",
            str(tmp_path),
            "https://example.com/live",
            "https://example.com/fin",
        ]
    )

    captured = capsys.readouterr()
    assert code == 1  # partial success: one refusal, one download
    assert "not enough free space to record" in captured.err
    assert "1 ok, 1 failed." in captured.err
    assert (tmp_path / "Some Channel" / "Best of 2024 [abc123XYZ].mp4").is_file()


def test_an_unmeasurable_volume_does_not_refuse_a_capture(
    tmp_path: Path, monkeypatch
) -> None:
    """A volume that cannot be measured lets the run proceed: the
    running check still applies once recording begins.
    """
    install_resolver(monkeypatch, {"https://example.com/live": BASIC_LIVE})
    _free_space(None)(monkeypatch)

    def executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"MKV HEAD + TS FRAGMENTS")
        return plan.target_path

    install_executor(monkeypatch, executor)

    code = main(["--folder", str(tmp_path), "https://example.com/live"])

    assert code == 0
    assert next((tmp_path / "Some Channel").iterdir()).suffix == ".mkv"


# ---------------------------------------------------------------------------
# AC: below the running floor, the transfer stops -- a clean shutdown
# ---------------------------------------------------------------------------


def test_the_running_floor_stops_a_capture_and_keeps_the_recording(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Space runs out mid-recording: the hook raises the KeyboardInterrupt
    a deliberate stop raises, so the shutdown path is the interrupt
    path's -- the recording is kept, playable, and the explanation
    names the floor, what was available, and what to do next.
    """
    install_resolver(monkeypatch, {"https://example.com/live": BASIC_LIVE})
    _free_space_sequence(3 * GB, 400 * MB)(monkeypatch)

    def executor(plan, progress):
        # yt-dlp has already appended fragments to the .part recording
        # when the check trips.
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.with_name(plan.target_path.name + ".part").write_bytes(
            b"FRAGMENTS SO FAR"
        )
        progress({"status": "downloading"})  # the watch measures here

    install_executor(monkeypatch, executor)

    code = main(["--folder", str(tmp_path), "https://example.com/live"])

    captured = capsys.readouterr()
    assert code == 130  # the deliberate-stop contract, reused
    err = captured.err
    assert "out of disk space" in err
    assert "500.0 MB" in err  # the floor
    assert "400.0 MB" in err  # what was available
    assert "Free up space" in err
    # What survived: the recording so far.
    kept = tmp_path / "Some Channel" / "Tonight's Stream [live999].mkv.part"
    assert kept.read_bytes() == b"FRAGMENTS SO FAR"
    assert f"recording kept at {kept}" in err


def test_a_swallowed_stop_still_ends_the_item_as_a_stop(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """On a real live fragment download yt-dlp converts the hook's
    KeyboardInterrupt into a graceful finish (it catches the interrupt
    for live streams and finalises the recording). The Queue must end
    the Item as a stop all the same, on the interrupt path, exit 130.
    """
    install_resolver(monkeypatch, {"https://example.com/live": BASIC_LIVE})
    _free_space_sequence(3 * GB, 400 * MB, 400 * MB)(monkeypatch)

    def executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"MKV HEAD + TS FRAGMENTS")
        try:
            progress({"status": "downloading"})  # the watch stops here
        except KeyboardInterrupt:
            pass  # fragment.py's live branch swallows it and finalises
        return plan.target_path  # a graceful finish: the file, finalised

    install_executor(monkeypatch, executor)

    code = main(["--folder", str(tmp_path), "https://example.com/live"])

    captured = capsys.readouterr()
    assert code == 130
    err = captured.err
    assert "out of disk space" in err
    target = tmp_path / "Some Channel" / "Tonight's Stream [live999].mkv"
    assert target.read_bytes() == b"MKV HEAD + TS FRAGMENTS"
    assert f"recording kept at {target}" in err


def test_the_running_floor_stops_the_queue_at_the_item_in_flight(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A nearly-full disk stops the whole Queue: later Items are named
    as not run, and earlier finished Items are untouched -- the
    interrupt contract exactly.
    """
    install_resolver(
        monkeypatch,
        {
            "https://example.com/live": BASIC_LIVE,
            "https://example.com/never": BASIC_DOWNLOAD,
        },
    )
    _free_space_sequence(3 * GB, 400 * MB, 400 * MB)(monkeypatch)

    def executor(plan, progress):
        if plan.url == "https://example.com/never":
            raise AssertionError("the Queue must have stopped before this")
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"FRAGMENTS SO FAR")
        progress({"status": "downloading"})

    install_executor(monkeypatch, executor)

    code = main(
        [
            "--folder",
            str(tmp_path),
            "https://example.com/live",
            "https://example.com/never",
        ]
    )

    captured = capsys.readouterr()
    assert code == 130
    assert "https://example.com/never" not in captured.out
    assert "Interrupted: 1 Item(s) were not run." in captured.err


def test_the_running_floor_stops_a_download_and_cleans_the_partials(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A Download stopped by the running check goes through the same
    Download aftermath as any interrupt: no partials, no unopenable
    file -- the Download is simply run again after space is freed.
    """
    install_resolver(monkeypatch, {"https://example.com/fin": BASIC_DOWNLOAD})
    _free_space(300 * MB)(monkeypatch)

    def executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        (plan.target_path.parent / (plan.target_path.name + ".part")).write_bytes(
            b"partial"
        )
        progress({"status": "downloading"})  # the watch measures here

    install_executor(monkeypatch, executor)

    code = main(["--folder", str(tmp_path), "https://example.com/fin"])

    captured = capsys.readouterr()
    assert code == 130
    assert "out of disk space" in captured.err
    assert "partial files removed" in captured.err
    # Nothing unopenable remains.
    held = (
        list((tmp_path / "Some Channel").glob("*"))
        if (tmp_path / "Some Channel").exists()
        else []
    )
    assert held == []


def test_a_transfer_with_room_is_not_bothered_by_the_watch(
    tmp_path: Path, monkeypatch
) -> None:
    """Above the running floor the watch is invisible: progress events
    pass through and the Item finishes normally.
    """
    install_resolver(
        monkeypatch,
        {"https://example.com/live": BASIC_LIVE},
    )
    _free_space(3 * GB)(monkeypatch)

    def executor(plan, progress):
        progress({"status": "downloading"})
        progress({"status": "downloading"})
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"MKV HEAD + TS FRAGMENTS")
        return plan.target_path

    install_executor(monkeypatch, executor)

    code = main(["--folder", str(tmp_path), "https://example.com/live"])

    assert code == 0
    target = next((tmp_path / "Some Channel").iterdir())
    assert target.read_bytes() == b"MKV HEAD + TS FRAGMENTS"
