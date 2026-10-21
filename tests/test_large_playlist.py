"""Large-playlist confirmation tests (ticket #15).

The acceptance criteria under test:

* A playlist above 50 Items asks for confirmation before starting.
* The prompt states how many Items were found.
* Declining exits without downloading anything.
* The limit is overridable by a flag.
* A playlist at or below the limit proceeds without prompting.

Every test goes through the CLI seam: arguments in; exit code, captured
output and files on disk out. The fakes installed through
``_fakes.py`` keep everything offline; the confirmation itself is a
CLI seam (``install_confirm``), so no test reads stdin.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _fakes import (
    BASIC_PLAYLIST,
    install_confirm,
    install_executor,
    install_resolver,
)

from vicxiun.cli import main

LINK = "https://example.com/playlist"
RECORD_NAME = ".vicxiun-done.txt"


def _playlist(count: int) -> dict[str, object]:
    """The playlist metadata with a chosen number of Items."""
    return {**BASIC_PLAYLIST, "playlist_count": count}


def _writing_executor(plan, progress):
    """A fake executor that writes the file the Plan promised."""
    plan.target_path.parent.mkdir(parents=True, exist_ok=True)
    plan.target_path.write_bytes(b"x")
    return plan.target_path


def _recording_confirm(counts: list[int]):
    """A confirmation that records the count it was asked about."""

    def confirm(count: int) -> bool:
        counts.append(count)
        return True

    return confirm


# ---------------------------------------------------------------------------
# AC: a playlist above 50 Items asks for confirmation before starting,
# and the prompt states how many Items were found
# ---------------------------------------------------------------------------


def test_a_playlist_above_the_limit_asks_and_states_the_count(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC (ticket #15): 60 Items > 50 -> ask, naming the count, then run."""
    install_resolver(monkeypatch, {LINK: _playlist(60)})
    install_executor(monkeypatch, _writing_executor)

    asked: list[int] = []
    install_confirm(monkeypatch, _recording_confirm(asked))

    code = main(["--folder", str(tmp_path), LINK])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    # The confirmation was asked exactly once, with the count found.
    assert asked == [60]
    # The prompt states how many Items were found.
    assert "This playlist holds 60 Items" in captured.err
    # Confirming means the Item is handled for real.
    assert (tmp_path / "Some Channel" / "A Big Mix [pl123].mp4").is_file()


# ---------------------------------------------------------------------------
# AC: declining exits without downloading anything
# ---------------------------------------------------------------------------


def test_declining_downloads_nothing_and_stops_the_queue(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC (ticket #15): saying no leaves the disk untouched.

    The declined playlist downloads nothing, is not written to the
    skip record (it is not done), and the Items after it do not start.
    """
    video = "https://example.com/video"
    install_resolver(
        monkeypatch,
        {LINK: _playlist(5000), video: {"video_id": "vid1"}},
    )

    def failing_if_called(plan, progress):  # pragma: no cover - guard
        raise AssertionError("nothing may be downloaded after a decline")

    install_executor(monkeypatch, failing_if_called)
    install_confirm(monkeypatch, lambda count: False)

    code = main(["--folder", str(tmp_path), LINK, video])

    captured = capsys.readouterr()
    # Declining is not a failure: nothing failed, so the exit is clean.
    assert code == 0, captured.err
    err = captured.err
    assert f"declined: {LINK} (not downloaded without confirmation)" in err
    assert "Declined: 1 Item(s) were not run." in err
    assert "0 ok, 1 declined, 0 failed." in err
    # Nothing was written: no file, and no skip record either.
    assert not any(p.is_file() for p in tmp_path.rglob("*"))
    assert not (tmp_path / RECORD_NAME).exists()


# ---------------------------------------------------------------------------
# AC: the limit is overridable by a flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("limit", "should_ask"),
    [
        (0, False),  # 0 turns the confirmation off.
        (10, True),  # Lowered: 60 is still above it.
        (100, False),  # Raised: 60 is now below it.
    ],
)
def test_the_limit_is_overridable_by_a_flag(
    tmp_path: Path,
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
    limit: int,
    should_ask: bool,
) -> None:
    """AC (ticket #15): --playlist-limit N replaces the default 50."""
    install_resolver(monkeypatch, {LINK: _playlist(60)})
    install_executor(monkeypatch, _writing_executor)

    asked: list[int] = []
    install_confirm(monkeypatch, _recording_confirm(asked))

    code = main(["--folder", str(tmp_path), "--playlist-limit", str(limit), LINK])

    assert code == 0, capsys.readouterr().err
    assert bool(asked) is should_ask
    if not should_ask:
        assert (tmp_path / "Some Channel" / "A Big Mix [pl123].mp4").is_file()


def test_a_negative_limit_is_a_user_error(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A negative --playlist-limit is bad arguments (exit 2)."""
    install_resolver(monkeypatch, {LINK: _playlist(60)})
    install_confirm(monkeypatch, lambda count: True)

    with pytest.raises(SystemExit) as exit_info:
        main(["--folder", str(tmp_path), "--playlist-limit", "-1", LINK])

    assert exit_info.value.code == 2
    assert "--playlist-limit must be 0 (off) or a positive number of Items" in (
        capsys.readouterr().err
    )


# ---------------------------------------------------------------------------
# AC: a playlist at or below the limit proceeds without prompting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 50])
def test_a_playlist_at_or_below_the_limit_never_asks(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str], count: int
) -> None:
    """AC (ticket #15): 50 Items at the default limit 50 -> no prompt."""
    install_resolver(monkeypatch, {LINK: _playlist(count)})
    install_executor(monkeypatch, _writing_executor)

    def failing_if_called(answer_count: int) -> bool:  # pragma: no cover - guard
        raise AssertionError("nothing may be asked at or below the limit")

    install_confirm(monkeypatch, failing_if_called)

    code = main(["--folder", str(tmp_path), LINK])

    assert code == 0, capsys.readouterr().err
    assert (tmp_path / "Some Channel" / "A Big Mix [pl123].mp4").is_file()


# ---------------------------------------------------------------------------
# The built-in interactive prompt, through the CLI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("y", True), ("Y", True), ("yes", True), ("n", False), ("", False)],
)
def test_the_builtin_prompt_reads_the_answer(
    tmp_path: Path,
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
    answer: str,
    expected: bool,
) -> None:
    """y/yes confirms; anything else (including empty) declines."""
    install_resolver(monkeypatch, {LINK: _playlist(60)})

    ran: list[str] = []

    def recording_executor(plan, progress):
        ran.append(plan.url)
        return _writing_executor(plan, progress)

    install_executor(monkeypatch, recording_executor)
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)

    code = main(["--folder", str(tmp_path), LINK])

    assert code == 0, capsys.readouterr().err
    assert (ran == [LINK]) is expected


def test_a_closed_stdin_declines(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """EOF on stdin (a piped script) declines: download nothing."""
    install_resolver(monkeypatch, {LINK: _playlist(60)})

    def failing_if_called(plan, progress):  # pragma: no cover - guard
        raise AssertionError("nothing may be downloaded after a decline")

    install_executor(monkeypatch, failing_if_called)

    def closed_input(prompt=""):  # pragma: no cover - guard
        raise EOFError

    monkeypatch.setattr("builtins.input", closed_input)

    code = main(["--folder", str(tmp_path), LINK])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "declined" in captured.err


# ---------------------------------------------------------------------------
# The count comes from the resolve, before anything else
# ---------------------------------------------------------------------------


def test_a_link_in_the_skip_record_never_asks(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A playlist whose link is in the skip record is skipped outright.

    The threshold counts every Item the playlist reports -- the user
    asked for them all either way (the #15 note in WARY.md) -- but a
    record-skip happens before any resolve, so no question is asked.
    """
    (tmp_path / RECORD_NAME).write_text(f"{LINK}\n", encoding="utf-8")

    # An empty mapping: if the resolver were reached at all, the link
    # would fail to resolve (exit 2) instead of being skipped (exit 0).
    install_resolver(monkeypatch, {})
    install_confirm(monkeypatch, lambda count: True)

    code = main(["--folder", str(tmp_path), LINK])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert f"{LINK}: skipped (in the skip record)" in captured.err


# ---------------------------------------------------------------------------
# A playlist that will not say how big it is is refused, not fetched
# ---------------------------------------------------------------------------


def test_a_playlist_without_a_count_is_refused(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A silent count must not bypass the confirmation.

    The count is what the confirmation works from: a playlist that
    reports none is a named failure (exit 2), never a download.
    """
    install_resolver(
        monkeypatch,
        {LINK: {"_type": "playlist", "title": "A Big Mix", "id": "pl123"}},
    )

    def failing_if_called(plan, progress):  # pragma: no cover - guard
        raise AssertionError("nothing may be downloaded without a count")

    install_executor(monkeypatch, failing_if_called)
    install_confirm(monkeypatch, lambda count: True)

    code = main(["--folder", str(tmp_path), LINK])

    captured = capsys.readouterr()
    assert code == 2, captured.err
    assert "does not say how many Items it holds" in captured.err
    assert not any(p.is_file() for p in tmp_path.rglob("*"))


def test_a_playlist_without_an_uploader_lands_in_the_fallback_group(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A playlist with no usable Uploader keeps the _unknown fallback."""
    install_resolver(
        monkeypatch,
        {
            LINK: {
                **_playlist(60),
                "uploader": "",
                "channel": "",
            }
        },
    )
    install_executor(monkeypatch, _writing_executor)
    install_confirm(monkeypatch, lambda count: True)

    code = main(["--folder", str(tmp_path), LINK])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert (tmp_path / "_unknown" / "A Big Mix [pl123].mp4").is_file()
