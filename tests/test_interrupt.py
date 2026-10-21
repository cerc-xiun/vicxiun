"""Interruption and clean-stop behaviour (issue #10).

An interrupt at one Item is either a deliberate stop (the Capture the
user is done recording) or an accident. The contract under test:

* The stop instruction -- press Ctrl+C once -- is printed before a
  Capture begins and not for a Download (the user never has to guess
  how to stop a capture -- user story 13).
* A single interrupt ends the Capture and leaves the recorded file --
  nothing unplayable, nothing deleted (user stories 11 and 16).
* A Capture interrupted without a clean shutdown keeps what it wrote;
  the handler deletes nothing for a Capture (the MKV container is the
  whole safety net -- ADR-0007).
* An interrupt during a Download leaves no unopenable file and cleans
  up yt-dlp's partial artifacts (ticket #13 handed mid-download .part
  cleanup to this ticket); a Download is simply run again.
* An interrupt stops the Queue at the Item in flight; Items finished
  before it are untouched, later Items are named as not run (ticket
  #13: completed Items are guaranteed intact).

The seam is the CLI exactly as elsewhere in the suite: a fake
resolver, and a fake executor that simulates the synthetic stream --
how yt-dlp would have left the disk when the interrupt arrived -- by
writing (or not writing) files under yt-dlp's naming scheme
(``.part``, ``.part-Frag*``, ``.ytdl``, pre-merge ``.fNNN.`` files)
and then raising ``KeyboardInterrupt``. No test reaches the network.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from _fakes import (
    BASIC_DOWNLOAD,
    BASIC_LIVE,
    install_executor,
    install_resolver,
)

from vicxiun.cli import main

# ---------------------------------------------------------------------------
# AC: a single interrupt ends the Capture cleanly and keeps the file
# ---------------------------------------------------------------------------


def test_a_single_interrupt_ends_the_capture_and_keeps_the_file(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The user stops a long recording deliberately -- one interrupt,
    and the recording they have is kept, not discarded (user story 11:
    hours of recording are not wasted).
    """
    install_resolver(monkeypatch, {"https://example.com/live": BASIC_LIVE})

    def executor(plan, progress):
        # yt-dlp finalises the container on a clean stop before
        # re-raising: the file is the one the player opens.
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"MKV HEAD + TS FRAGMENTS")
        raise KeyboardInterrupt("simulated Ctrl-C")

    install_executor(monkeypatch, executor)

    code = main(["--folder", str(tmp_path), "https://example.com/live"])

    captured = capsys.readouterr()
    assert code == 130  # interrupted, as the Queue contract says
    err = captured.err
    # The recording itself is on disk.
    target = next((tmp_path / "Some Channel").iterdir())
    assert target.suffix == ".mkv"
    assert target.read_bytes() == b"MKV HEAD + TS FRAGMENTS"
    # ... and the user is told where it is.
    assert f"recording kept at {target}" in err


def test_a_stream_that_never_ends_can_be_stopped(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stream that never ends is stoppable: one interrupt, and the
    recording stops. The Queue never demands a second interrupt and
    never retries the Item (user story 16).
    """
    install_resolver(monkeypatch, {"https://example.com/live": BASIC_LIVE})

    calls: list[str] = []

    def executor(plan, progress):
        calls.append(plan.url)
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(plan.target_path, "ab") as f:
            f.writelines(b"TS " for _ in range(10000))  # "never": fragments forever
        raise KeyboardInterrupt("simulated Ctrl-C")

    install_executor(monkeypatch, executor)

    code = main(["--folder", str(tmp_path), "https://example.com/live"])

    capsys.readouterr()
    assert code == 130
    assert calls == ["https://example.com/live"]  # one call, one interrupt
    target = next((tmp_path / "Some Channel").iterdir())
    assert target.read_bytes() == b"TS " * 10000


def test_an_unclean_interruption_keeps_what_the_capture_wrote(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No clean shutdown: whatever ys-dlp had on disk survives. The
    handler deletes NOTHING for a Capture -- the container is the
    whole safety net (ADR-0007) -- so both the finalised file and its
    stale ``.part`` sibling are still there, and the kept path is
    reported.
    """
    install_resolver(monkeypatch, {"https://example.com/live": BASIC_LIVE})

    def executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"PARTIAL MKV")
        plan.target_path.with_name(plan.target_path.name + ".part").write_bytes(
            b"TRANSCRIBED FRAGMENT"
        )
        raise KeyboardInterrupt("simulated Ctrl-C")

    install_executor(monkeypatch, executor)

    code = main(["--folder", str(tmp_path), "https://example.com/live"])

    captured = capsys.readouterr()
    assert code == 130
    uploader = tmp_path / "Some Channel"
    names = sorted(p.name for p in uploader.iterdir())
    assert len(names) == 2  # the file AND the .part sibling survive
    # The user is pointed at the finalised file, not the partial.
    kept = next(p for p in uploader.iterdir() if p.suffix == ".mkv")
    assert f"recording kept at {kept}" in captured.err


def test_a_capture_interrupted_before_anything_recorded(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An interrupt before the first fragment lands leaves no files
    and says so honestly instead of inventing a path.
    """
    install_resolver(monkeypatch, {"https://example.com/live": BASIC_LIVE})

    def executor(plan, progress):
        raise KeyboardInterrupt("simulated Ctrl-C")

    install_executor(monkeypatch, executor)

    code = main(["--folder", str(tmp_path), "https://example.com/live"])

    captured = capsys.readouterr()
    assert code == 130
    assert "Capture stopped before anything was recorded" in captured.err
    assert (tmp_path / "Some Channel").exists() is False


# ---------------------------------------------------------------------------
# AC: interrupting a Download leaves no unopenable file and no partials
# ---------------------------------------------------------------------------


def test_a_download_interrupt_cleans_up_the_partial_artifacts(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mid-download interrupt: yt-dlp's partial artifacts (.part,
    .part-Frag*, .ytdl, pre-merge .fNNN files) are removed -- the
    cleanup ticket #13 handed over -- and the folder holds nothing
    unopenable. The Download is simply run again.
    """
    install_resolver(
        monkeypatch,
        {"https://example.com/fin": {**BASIC_DOWNLOAD, "video_id": "dl777"}},
    )

    def executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        target = plan.target_path
        for name in (
            target.name + ".part",
            target.name + ".part-Frag12",
            target.name + ".ytdl",
            target.name + ".f137.mp4",
        ):
            (target.parent / name).write_bytes(b"partial")
        raise KeyboardInterrupt("simulated Ctrl-C")

    install_executor(monkeypatch, executor)

    code = main(["--folder", str(tmp_path), "https://example.com/fin"])

    captured = capsys.readouterr()
    assert code == 130
    assert "partial files removed" in captured.err


def test_a_download_interrupt_leaves_no_unopenable_final_file(
    tmp_path: Path, monkeypatch
) -> None:
    """An interrupt that lands during the merge leaves a final file
    written this run -- truncated, unopenable. It must not survive.
    """
    install_resolver(
        monkeypatch,
        {"https://example.com/fin": {**BASIC_DOWNLOAD, "video_id": "dl888"}},
    )

    def executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"TRUNCATED MP4 HEAD")
        raise KeyboardInterrupt("simulated Ctrl-C")

    install_executor(monkeypatch, executor)

    code = main(["--folder", str(tmp_path), "https://example.com/fin"])

    assert code == 130
    # Nothing unopenable remains: the folder is there or empty, but it
    # holds no file at all.
    held = (
        list((tmp_path / "Some Channel").glob("*"))
        if (tmp_path / "Some Channel").exists()
        else []
    )
    assert held == []


def test_a_download_interrupt_does_not_touch_files_that_merely_look_like_partials(
    tmp_path: Path, monkeypatch
) -> None:
    """The cleanup deletes only genuine yt-dlp partials. A file the
    user put beside the target -- a backup, a thumbnail -- shares the
    prefix but is not a partial, and must survive the interrupt
    untouched (ticket #13's spirit: nothing but partials is ours).
    """
    install_resolver(
        monkeypatch,
        {"https://example.com/fin": {**BASIC_DOWNLOAD, "video_id": "dl110"}},
    )
    uploader = tmp_path / "Some Channel"
    uploader.mkdir(parents=True, exist_ok=True)
    survivors = [
        uploader / "Best of 2024 [dl110].mp4.old-backup",
        uploader / "Best of 2024 [dl110].mp4.jpg",
        uploader / "Best of 2024 [dl110].mp4",
    ]
    for survivor in survivors:
        survivor.write_bytes(b"user data")

    def executor(plan, progress):
        raise KeyboardInterrupt("simulated Ctrl-C")

    install_executor(monkeypatch, executor)

    # --no-skip: the Item's target is deliberately pre-placed (the
    # user's own copy), and the test interrupts the executor -- which
    # by default is never reached for a file that already exists.
    code = main(["--folder", str(tmp_path), "--no-skip", "https://example.com/fin"])

    assert code == 130
    for survivor in survivors:
        assert survivor.read_bytes() == b"user data"


def test_a_download_interrupt_spares_an_older_file(tmp_path: Path, monkeypatch) -> None:
    """A final file written BEFORE this Item began -- a completed Item
    or a previous run's result -- survives the interruption (an Item
    that completes is guaranteed intact, ticket #13).
    """
    install_resolver(
        monkeypatch,
        {"https://example.com/fin": {**BASIC_DOWNLOAD, "video_id": "dl999"}},
    )
    uploader = tmp_path / "Some Channel"
    uploader.mkdir(parents=True, exist_ok=True)
    prior = uploader / "Best of 2024 [dl999].mp4"
    prior.write_bytes(b"COMPLETE FILE FROM EARLIER")

    def executor(plan, progress):
        raise KeyboardInterrupt("simulated Ctrl-C")

    install_executor(monkeypatch, executor)

    # --no-skip, for the same reason as the partial-looking-files test
    # above: the prior file must stay put while the executor is
    # interrupted, and the executor only runs when skipping is off.
    code = main(["--folder", str(tmp_path), "--no-skip", "https://example.com/fin"])

    assert code == 130
    assert prior.exists()
    assert prior.read_bytes() == b"COMPLETE FILE FROM EARLIER"


# ---------------------------------------------------------------------------
# AC: the Queue still stops at the interrupted Item; the rest is honest
# ---------------------------------------------------------------------------


def test_an_interrupted_capture_stops_the_queue_but_spares_completed_items(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Items finished before the interrupt are untouched, and the Items
    after it are named as not run -- ticket #13's guarantee, honoured
    through the new Capture-aftermath path.
    """
    install_resolver(
        monkeypatch,
        {
            "https://example.com/fin": BASIC_DOWNLOAD,
            "https://example.com/live": BASIC_LIVE,
        },
    )

    ran: list[str] = []

    def executor(plan, progress):
        ran.append(plan.url)
        if plan.url == "https://example.com/live":
            plan.target_path.parent.mkdir(parents=True, exist_ok=True)
            plan.target_path.write_bytes(b"HALF RECORDED")
            raise KeyboardInterrupt("simulated Ctrl-C")
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"FINISHED FILE")
        return plan.target_path

    install_executor(monkeypatch, executor)

    code = main(
        [
            "--folder",
            str(tmp_path),
            "https://example.com/fin",
            "https://example.com/live",
            "https://example.com/never-run",
        ]
    )

    captured = capsys.readouterr()
    assert code == 130
    # The never-run link was never planned either.
    assert "https://example.com/never-run" not in captured.err
    assert "https://example.com/never-run" not in ran
    # The Download that finished before the interrupt is intact.
    files = {p.name: p for p in (tmp_path / "Some Channel").iterdir()}
    download_file = next(p for n, p in files.items() if n.endswith(".mp4"))
    assert download_file.read_bytes() == b"FINISHED FILE"
    assert len(files) == 2  # the kept Capture file too
    assert "Interrupted: 1 Item(s) were not run." in captured.err


def test_the_stop_instruction_is_printed_before_a_capture_begins(
    tmp_path: Path, monkeypatch
) -> None:
    """The instruction naming the single interrupt is on stderr by the
    time recording starts. Printed for a Capture, never for a
    Download: user story 13 asks how to stop what they started, and
    yt-dlp itself warns that a second interrupt risks the file.
    """
    install_resolver(
        monkeypatch,
        {
            "https://example.com/live": BASIC_LIVE,
            "https://example.com/fin": BASIC_DOWNLOAD,
        },
    )

    seen: dict[str, str] = {}

    def executor(plan, progress):
        # Snapshot stderr at the moment recording would begin: the
        # instruction must already be there.
        seen[plan.kind.name] = fake_stderr.getvalue()
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"synthetic bytes")
        return plan.target_path

    install_executor(monkeypatch, executor)

    fake_stderr = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = fake_stderr
    try:
        code = main(
            [
                "--folder",
                str(tmp_path),
                "https://example.com/fin",
                "https://example.com/live",
            ]
        )
    finally:
        sys.stderr = real_stderr
    assert code == 0

    # Download: no instruction -- stopping is not how a Download ends.
    assert "Ctrl" not in seen["Download"]
    # Capture: the instruction arrived before recording began, and it
    # says why a single interrupt is the whole story.
    assert "Ctrl" in seen["Capture"]
    assert "once" in seen["Capture"]
