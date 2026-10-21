"""Waiting for a scheduled stream through the CLI (ticket #11).

The user starts the tool before a stream begins and walks away: the
tool polls at a configurable interval (default 60 seconds), the
waiting state is visible on stderr, and recording begins from the
beginning the moment the stream goes live. With ``--dry-run`` the tool
reports that it would wait and returns at once.

The engine seam is faked exactly as elsewhere in the suite; the poll
sleep is faked too, so no test ever really waits.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from vicxiun.cli import main
from vicxiun.errors import UserError
from vicxiun.plan import CaptureKind, DownloadKind, Plan

SCHEDULED: dict[str, object] = {
    "title": "Tonight's Stream",
    "uploader": "Some Channel",
    "ext": "mkv",
    "is_live": False,
    "live_status": "is_upcoming",
    "format_description": "MKV, bestvideo+bestaudio recorded until stopped",
    "video_id": "sched1",
}

LIVE: dict[str, object] = {
    "title": "Tonight's Stream",
    "uploader": "Some Channel",
    "ext": "mkv",
    "is_live": True,
    "live_status": "is_live",
    "format_description": "MKV, bestvideo+bestaudio recorded until stopped",
    "video_id": "sched1",
}

# The stream ended (and the platform kept an archive) before a check
# ever saw it live.
ENDED: dict[str, object] = {
    "title": "Tonight's Stream",
    "uploader": "Some Channel",
    "ext": "mp4",
    "is_live": False,
    "live_status": "post_live",
    "format_description": "MP4, H.264 + AAC",
    "video_id": "sched1",
}

LINK = "https://example.com/scheduled"


def _writing_executor(plan: Plan, progress) -> Path:
    """A fake executor that writes the file the Plan promised."""
    plan.target_path.parent.mkdir(parents=True, exist_ok=True)
    plan.target_path.write_bytes(b"recording")
    return plan.target_path


def _recording_executor(
    ran: list[Plan],
) -> Callable[[Plan, object], Path]:
    """An executor that records each Plan it is asked to run."""

    def executor(plan: Plan, progress) -> Path:
        ran.append(plan)
        return _writing_executor(plan, progress)

    return executor


def _fake_sleep(slept: list[int | float]) -> Callable[[int | float], None]:
    """Replace ``time.sleep`` with a recorder (and nothing real waits)."""

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    return fake_sleep


def _install_stateful_resolver(
    monkeypatch: pytest.MonkeyPatch,
    states: list[dict[str, object]],
) -> None:
    """Install a resolver that returns ``states`` in order, then the last.

    The first call is the initial Plan; every later call is one poll.
    The last state repeats so the wait always terminates.
    """
    calls: dict[str, int] = {"n": 0}
    # The fake prereq check stub the CLI seam expects.
    monkeypatch.setattr("vicxiun.cli._prereq_check", lambda: 0)

    def resolver(url: str) -> dict[str, object]:
        state = states[min(calls["n"], len(states) - 1)]
        calls["n"] += 1
        return dict(state)

    monkeypatch.setattr("vicxiun.cli._resolver", resolver)


# ---------------------------------------------------------------------------
# AC: recording begins automatically when the stream starts
# ---------------------------------------------------------------------------


def test_a_scheduled_stream_is_recorded_from_its_beginning_when_it_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _install_stateful_resolver(monkeypatch, [SCHEDULED, LIVE])
    monkeypatch.setattr("vicxiun.queue.time.sleep", _fake_sleep(slept := []))
    ran: list[Plan] = []
    monkeypatch.setattr("vicxiun.cli._executor", _recording_executor(ran))

    code = main(["--wait-for-stream", "--folder", str(tmp_path), LINK])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert len(ran) == 1
    # The recorded Item is a live Capture: the engine records it from
    # the beginning (live_from_start), which is the ticket's promise.
    assert isinstance(ran[0].kind, CaptureKind)
    assert ran[0].kind.is_live is True
    assert (tmp_path / "Some Channel").is_dir()
    assert slept == [60]  # one poll at the default interval, then live


# ---------------------------------------------------------------------------
# AC: the poll interval is configurable, defaulting to 60 seconds
# ---------------------------------------------------------------------------


def test_the_poll_defaults_to_60_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _install_stateful_resolver(monkeypatch, [SCHEDULED, SCHEDULED, LIVE])
    monkeypatch.setattr("vicxiun.queue.time.sleep", _fake_sleep(slept := []))
    ran: list[Plan] = []
    monkeypatch.setattr("vicxiun.cli._executor", _recording_executor(ran))

    code = main(["--wait-for-stream", "--folder", str(tmp_path), LINK])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert slept == [60, 60]
    assert len(ran) == 1


def test_the_poll_interval_is_configurable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _install_stateful_resolver(monkeypatch, [SCHEDULED, SCHEDULED, LIVE])
    monkeypatch.setattr("vicxiun.queue.time.sleep", _fake_sleep(slept := []))
    ran: list[Plan] = []
    monkeypatch.setattr("vicxiun.cli._executor", _recording_executor(ran))

    code = main(
        [
            "--wait-for-stream",
            "--wait-interval",
            "7",
            "--folder",
            str(tmp_path),
            LINK,
        ]
    )

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert slept == [7, 7]
    assert len(ran) == 1


@pytest.mark.parametrize("bad", ["0", "-5", "abc"])
def test_the_wait_interval_must_be_a_positive_whole_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    _install_stateful_resolver(monkeypatch, [SCHEDULED])
    with pytest.raises(SystemExit) as exit_info:
        main(["--wait-interval", bad, "--folder", str(tmp_path), LINK])
    assert exit_info.value.code == 2


# ---------------------------------------------------------------------------
# AC: the waiting state is visible
# ---------------------------------------------------------------------------


def test_the_waiting_state_is_visible_while_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _install_stateful_resolver(monkeypatch, [SCHEDULED, SCHEDULED, LIVE])
    monkeypatch.setattr("vicxiun.queue.time.sleep", _fake_sleep([]))
    ran: list[Plan] = []
    monkeypatch.setattr("vicxiun.cli._executor", _recording_executor(ran))

    code = main(["--wait-for-stream", "--folder", str(tmp_path), LINK])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    err = captured.err
    assert "waiting: the stream has not started; checking every 60s" in err
    assert "blocks the rest of the Queue" in err
    assert "still waiting (the stream has not started)" in err
    assert "The stream has started; recording from the beginning." in err
    assert len(ran) == 1


def test_a_failed_check_keeps_waiting_instead_of_losing_the_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """One network hiccup must not end a capture the user walked away from."""
    calls: dict[str, int] = {"n": 0}
    monkeypatch.setattr("vicxiun.cli._prereq_check", lambda: 0)

    def resolver(url: str) -> dict[str, object]:
        calls["n"] += 1
        if calls["n"] == 2:  # the first re-check fails
            raise UserError("network hiccup")
        return dict(SCHEDULED if calls["n"] < 3 else LIVE)

    monkeypatch.setattr("vicxiun.cli._resolver", resolver)
    monkeypatch.setattr("vicxiun.queue.time.sleep", _fake_sleep([]))
    ran: list[Plan] = []
    monkeypatch.setattr("vicxiun.cli._executor", _recording_executor(ran))

    code = main(["--wait-for-stream", "--folder", str(tmp_path), LINK])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "the last check failed" in captured.err
    assert len(ran) == 1
    assert isinstance(ran[0].kind, CaptureKind)


def test_a_stream_that_ends_before_any_check_is_fetched_as_the_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """If the stream is over before a check saw it live, fetch the archive.

    Waiting forever for a stream that will never be live again would
    lose the recording entirely; the end state is reported honestly.
    """
    _install_stateful_resolver(monkeypatch, [SCHEDULED, ENDED])
    monkeypatch.setattr("vicxiun.queue.time.sleep", _fake_sleep([]))
    ran: list[Plan] = []
    monkeypatch.setattr("vicxiun.cli._executor", _recording_executor(ran))

    code = main(["--wait-for-stream", "--folder", str(tmp_path), LINK])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert len(ran) == 1
    assert isinstance(ran[0].kind, DownloadKind)
    assert "ended before recording could begin" in captured.err


# ---------------------------------------------------------------------------
# AC: with --dry-run, it reports it would wait and returns immediately
# ---------------------------------------------------------------------------


def test_dry_run_reports_it_would_wait_for_a_scheduled_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _install_stateful_resolver(monkeypatch, [SCHEDULED])
    monkeypatch.setattr("vicxiun.queue.time.sleep", _fake_sleep(slept := []))

    code = main(["--dry-run", "--wait-for-stream", "--folder", str(tmp_path), LINK])

    captured = capsys.readouterr()
    assert code == 0, captured.out
    assert "Capture" in captured.out
    assert "would wait for the stream to start" in captured.out
    assert slept == []  # the preview never waits


# ---------------------------------------------------------------------------
# Without --wait-for-stream, a scheduled link behaves as before
# ---------------------------------------------------------------------------


def test_a_scheduled_stream_without_the_flag_is_handled_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _install_stateful_resolver(monkeypatch, [SCHEDULED])
    monkeypatch.setattr("vicxiun.queue.time.sleep", _fake_sleep(slept := []))
    ran: list[Plan] = []
    monkeypatch.setattr("vicxiun.cli._executor", _recording_executor(ran))

    code = main(["--folder", str(tmp_path), LINK])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert len(ran) == 1
    assert isinstance(ran[0].kind, DownloadKind)
    assert "waiting" not in captured.err
    assert slept == []


# ---------------------------------------------------------------------------
# AC: a waiting Item blocks the Queue (documented; enforced by sequence)
# ---------------------------------------------------------------------------


def test_an_interrupt_during_the_wait_stops_the_queue_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Ctrl+C while waiting is exit 130, later Items are named not run."""

    def interrupting_sleep(seconds: float) -> None:
        raise KeyboardInterrupt()

    _install_stateful_resolver(monkeypatch, [SCHEDULED])
    monkeypatch.setattr("vicxiun.queue.time.sleep", interrupting_sleep)
    monkeypatch.setattr("vicxiun.cli._executor", _writing_executor)

    code = main(
        [
            "--wait-for-stream",
            "--folder",
            str(tmp_path),
            LINK,
            "https://example.com/never-reached",
        ]
    )

    captured = capsys.readouterr()
    assert code == 130, captured.err
    assert "Interrupted: 1 Item(s) were not run." in captured.err
