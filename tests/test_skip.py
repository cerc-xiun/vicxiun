"""Skip-existing tests (ticket #14).

The acceptance criteria under test:

* An Item already present is skipped by default.
* The skip record lives alongside the output, so re-running a Queue is
  safe.
* Re-running a completed Queue downloads nothing.
* Skipping can be turned off.

Every test goes through the CLI seam: arguments in; exit code, captured
stderr and files on disk out. The fakes installed through ``_fakes.py``
keep everything offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _fakes import (
    BASIC_DOWNLOAD,
    install_executor,
    install_resolver,
)

from vicxiun.cli import main
from vicxiun.errors import UserError

RECORD_NAME = ".vicxiun-done.txt"


def _meta(suffix: str) -> dict[str, object]:
    """Metadata with a distinct id, so two links never share a target."""
    return {**BASIC_DOWNLOAD, "video_id": suffix}


def _writing_executor(plan, progress):
    """A fake executor that writes the file the Plan promised."""
    plan.target_path.parent.mkdir(parents=True, exist_ok=True)
    plan.target_path.write_bytes(b"x")
    return plan.target_path


# ---------------------------------------------------------------------------
# AC: an Item already present is skipped by default
# ---------------------------------------------------------------------------


def test_a_file_already_present_is_skipped(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The file is on disk from an earlier, record-less run: skip it.

    The record is deleted to force the skip decision to come from the
    file itself (the resolve runs, the executor must not).
    """
    install_resolver(monkeypatch, {"https://example.com/a": BASIC_DOWNLOAD})

    install_executor(monkeypatch, _writing_executor)
    assert main(["--folder", str(tmp_path), "https://example.com/a"]) == 0
    assert (tmp_path / RECORD_NAME).read_text(encoding="utf-8") == (
        "https://example.com/a\n"
    )

    # Second run with the record gone: the file alone decides.
    (tmp_path / RECORD_NAME).unlink()

    def failing_if_called(plan, progress):  # pragma: no cover - guard
        raise AssertionError("executor must not run for a skipped Item")

    install_executor(monkeypatch, failing_if_called)
    code = main(["--folder", str(tmp_path), "https://example.com/a"])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    err = captured.err
    assert (
        f"skipped: https://example.com/a (file already present: "
        f"{tmp_path / 'Some Channel' / 'Best of 2024 [abc123XYZ].mp4'})"
    ) in err
    assert "1 skipped" in err


# ---------------------------------------------------------------------------
# AC: the skip record lives alongside the output
# ---------------------------------------------------------------------------


def test_the_record_lives_alongside_the_output(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC (ticket #14): the record sits in the output folder itself.

    It is not a history: only finished Items are named, one URL per
    line, and a failed Item is never recorded (so a re-run retries it).
    """
    install_resolver(
        monkeypatch,
        {
            "https://example.com/good": _meta("good"),
            "https://example.com/dead": _meta("dead"),
        },
    )

    def failing_middle(plan, progress):
        if plan.url == "https://example.com/dead":
            raise UserError("video removed by uploader")
        return _writing_executor(plan, progress)

    install_executor(monkeypatch, failing_middle)

    code = main(
        [
            "--folder",
            str(tmp_path),
            "https://example.com/good",
            "https://example.com/dead",
        ]
    )

    assert code == 1  # partial success
    record = tmp_path / RECORD_NAME
    assert record.exists()
    assert record.parent == tmp_path
    # Only the finished Item is in it; the failure is left for a retry.
    assert record.read_text(encoding="utf-8") == "https://example.com/good\n"


# ---------------------------------------------------------------------------
# AC: re-running a completed Queue downloads nothing
# ---------------------------------------------------------------------------


def test_rerunning_a_completed_queue_downloads_nothing(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC (ticket #14): the second run skips by record, executor idle."""
    links = ["https://example.com/a", "https://example.com/b"]
    install_resolver(
        monkeypatch,
        {
            "https://example.com/a": _meta("a"),
            "https://example.com/b": _meta("b"),
        },
    )

    install_executor(monkeypatch, _writing_executor)
    assert main(["--folder", str(tmp_path), *links]) == 0

    def failing_if_called(plan, progress):  # pragma: no cover - guard
        raise AssertionError("executor must not run for a skipped Item")

    install_executor(monkeypatch, failing_if_called)
    code = main(["--folder", str(tmp_path), *links])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    err = captured.err
    assert "skipped: https://example.com/a (in the skip record)" in err
    assert "skipped: https://example.com/b (in the skip record)" in err
    assert "0 ok, 2 skipped, 0 failed." in err


def test_a_failed_item_is_retried_on_the_next_run(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failure is not "already have it": the re-run retries that Item."""
    install_resolver(
        monkeypatch,
        {
            "https://example.com/good": _meta("good"),
            "https://example.com/dead": _meta("dead"),
        },
    )

    def failing_on_dead(plan, progress):
        if plan.url == "https://example.com/dead":
            raise UserError("video removed by uploader")
        return _writing_executor(plan, progress)

    install_executor(monkeypatch, failing_on_dead)
    assert (
        main(
            [
                "--folder",
                str(tmp_path),
                "https://example.com/good",
                "https://example.com/dead",
            ]
        )
        == 1
    )

    retried: list[str] = []

    def recording_retry(plan, progress):
        retried.append(plan.url)
        return failing_on_dead(plan, progress)

    install_executor(monkeypatch, recording_retry)
    code = main(
        [
            "--folder",
            str(tmp_path),
            "https://example.com/good",
            "https://example.com/dead",
        ]
    )

    captured = capsys.readouterr()
    # Only the failure was retried; the finished Item was skipped.
    assert retried == ["https://example.com/dead"]
    assert code == 2  # everything that ran, failed
    assert "0 ok, 1 skipped, 1 failed." in captured.err


def test_completed_items_survive_an_interrupt_as_skips(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An interrupt keeps finished Items in the record; the re-run skips them."""
    links = ["https://example.com/a", "https://example.com/b"]
    install_resolver(
        monkeypatch,
        {
            "https://example.com/a": _meta("a"),
            "https://example.com/b": _meta("b"),
        },
    )

    def interrupt_on_b(plan, progress):
        if plan.url == "https://example.com/b":
            raise KeyboardInterrupt("simulated Ctrl-C")
        return _writing_executor(plan, progress)

    install_executor(monkeypatch, interrupt_on_b)
    assert main(["--folder", str(tmp_path), *links]) == 130

    ran: list[str] = []

    def recording_second_run(plan, progress):
        ran.append(plan.url)
        return _writing_executor(plan, progress)

    install_executor(monkeypatch, recording_second_run)
    code = main(["--folder", str(tmp_path), *links])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    # Item a was already finished when the interrupt arrived, so the
    # second run must not touch it again.
    assert ran == ["https://example.com/b"]
    assert "1 skipped" in captured.err


# ---------------------------------------------------------------------------
# AC: skipping can be turned off
# ---------------------------------------------------------------------------


def test_no_skip_processes_every_link_again(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC (ticket #14): --no-skip runs the executor again, files overwritten."""
    links = ["https://example.com/a", "https://example.com/b"]
    install_resolver(
        monkeypatch,
        {
            "https://example.com/a": _meta("a"),
            "https://example.com/b": _meta("b"),
        },
    )

    install_executor(monkeypatch, _writing_executor)
    assert main(["--folder", str(tmp_path), *links]) == 0

    ran: list[str] = []

    def overwriting_executor(plan, progress):
        ran.append(plan.url)
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"y")
        return plan.target_path

    install_executor(monkeypatch, overwriting_executor)
    code = main(["--folder", str(tmp_path), "--no-skip", *links])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert ran == links
    # The files were genuinely written again, not skipped.
    for file_id in ("a", "b"):
        assert (
            tmp_path / "Some Channel" / f"Best of 2024 [{file_id}].mp4"
        ).read_bytes() == b"y"
    # No skipped segment in the counts: nothing was skipped.
    assert "2 ok, 0 failed." in captured.err
