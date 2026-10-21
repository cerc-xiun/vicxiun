"""Queue behaviour tests (ticket #13).

The acceptance criteria under test:

* Several links pass in one command and are processed one after
  another -- strictly sequentially, no concurrency.
* A failed Item does not stop the rest.
* A summary at the end lists what succeeded, what failed, and why.
* The exit code reflects partial success when some Items failed.

Every test goes through the CLI seam: arguments in; exit code, captured
stderr and files on disk out. The fakes installed through
``_fakes.py`` keep everything offline. The interrupt criteria
(stopping without corrupting completed Items) are exercised in
``test_execute.py`` and here.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from _fakes import (
    BASIC_DOWNLOAD,
    install_executor,
    install_resolver,
)

from vicxiun.cli import main
from vicxiun.errors import RefusedContent, UserError


def _writing_executor(plan, progress):
    """A fake executor that writes the file the Plan promised."""
    plan.target_path.parent.mkdir(parents=True, exist_ok=True)
    plan.target_path.write_bytes(b"x")
    return plan.target_path


# ---------------------------------------------------------------------------
# AC: several links in one command, processed one after another
# ---------------------------------------------------------------------------


def test_several_links_in_one_command_are_processed_one_after_another(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC (ticket #13): several links, one command, one after another.

    The Queue must handle each Item in the order the user typed them,
    on a single thread: strictly sequential, no concurrency.
    """
    links = [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    install_resolver(
        monkeypatch,
        {link: {**BASIC_DOWNLOAD, "video_id": f"id{link[-1]}"} for link in links},
    )

    ran: list[str] = []
    threads: set[int] = set()

    def recording_executor(plan, progress):
        ran.append(plan.url)
        threads.add(threading.get_ident())
        return _writing_executor(plan, progress)

    install_executor(monkeypatch, recording_executor)

    code = main(["--folder", str(tmp_path), *links])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    # In link order, every Item handled.
    assert ran == links
    # A single thread handled the whole Queue: no concurrency.
    assert len(threads) == 1


# ---------------------------------------------------------------------------
# AC: a failed Item does not stop the rest
# ---------------------------------------------------------------------------


def test_a_failed_item_does_not_stop_the_rest(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC (ticket #13): one dead link does not waste the session.

    The middle Item fails inside the executor; the Queue must still
    handle the third Item and produce all three result lines.
    """
    links = [
        "https://example.com/a",
        "https://example.com/dead",
        "https://example.com/c",
    ]
    install_resolver(
        monkeypatch,
        {
            "https://example.com/a": {**BASIC_DOWNLOAD, "video_id": "idAAA"},
            "https://example.com/dead": {
                **BASIC_DOWNLOAD,
                "video_id": "idDEAD",
            },
            "https://example.com/c": {
                **BASIC_DOWNLOAD,
                "video_id": "idCCC",
            },
        },
    )

    def failing_middle(plan, progress):
        if plan.url == "https://example.com/dead":
            raise UserError("video removed by uploader")
        return _writing_executor(plan, progress)

    install_executor(monkeypatch, failing_middle)

    code = main(["--folder", str(tmp_path), *links])

    captured = capsys.readouterr()
    # Partial success: Items a and c landed, middle Item failed.
    assert code == 1
    files = sorted(p.name for p in (tmp_path / "Some Channel").iterdir())
    assert len(files) == 2
    # The failed Item and both successes are all reported.
    assert "https://example.com/dead" in captured.err
    assert "https://example.com/a" in captured.err
    assert "https://example.com/c" in captured.err


def test_a_failed_resolution_does_not_stop_the_rest(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A link the resolver cannot even plan must not stop the rest.

    The second link is absent from the resolver mapping, so planning
    itself fails; the Queue continues to the third Item.
    """
    install_resolver(
        monkeypatch,
        {
            "https://example.com/a": BASIC_DOWNLOAD,
            "https://example.com/c": {
                **BASIC_DOWNLOAD,
                "video_id": "idCCC",
            },
            # "https://example.com/dead" is intentionally unmapped.
        },
    )
    install_executor(monkeypatch, _writing_executor)

    code = main(
        [
            "--folder",
            str(tmp_path),
            "https://example.com/a",
            "https://example.com/dead",
            "https://example.com/c",
        ]
    )

    captured = capsys.readouterr()
    assert code == 1
    # Both healthy Items landed on disk.
    files = sorted(p.name for p in (tmp_path / "Some Channel").iterdir())
    assert len(files) == 2
    # The unresolvable link was reported, not swallowed.
    assert "https://example.com/dead" in captured.err


# ---------------------------------------------------------------------------
# AC: a summary at the end lists what succeeded, what failed, and why
# ---------------------------------------------------------------------------


def test_the_summary_lists_what_succeeded_what_failed_and_why(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC (ticket #13): the closing summary is itemised.

    One line per Item -- the link, whether it succeeded or failed, and
    the reason -- followed by the counts.
    """
    install_resolver(
        monkeypatch,
        {
            "https://example.com/good": {
                **BASIC_DOWNLOAD,
                "video_id": "idGOOD",
            },
            "https://example.com/drm": {
                **BASIC_DOWNLOAD,
                "video_id": "idDRM",
            },
        },
    )

    def refusing_on_drm(plan, progress):
        if plan.url == "https://example.com/drm":
            raise RefusedContent("DRM")
        return _writing_executor(plan, progress)

    install_executor(monkeypatch, refusing_on_drm)

    code = main(
        [
            "--folder",
            str(tmp_path),
            "https://example.com/good",
            "https://example.com/drm",
        ]
    )

    captured = capsys.readouterr()
    assert code == 1  # partial success
    err = captured.err
    # What succeeded (with where it landed).
    ok_line = next(
        line
        for line in err.splitlines()
        if line.startswith("ok: https://example.com/good")
    )
    assert str(tmp_path) in ok_line
    # What failed, and why.
    assert "failed: https://example.com/drm (refused: DRM)" in err
    # The closing counts line.
    assert "1 ok, 1 failed." in err


# ---------------------------------------------------------------------------
# AC: the exit code reflects partial success when some Items failed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failing_count", "total", "expected"),
    [
        # Some succeeded, some failed -> partial success (code 1).
        (1, 3, 1),
        # Every Item succeeded -> 0.
        (0, 3, 0),
        # Every Item failed -> the most-specific named cause's code (2).
        (3, 3, 2),
    ],
)
def test_the_exit_code_reflects_the_mix_of_outcomes(
    tmp_path: Path,
    monkeypatch,
    failing_count: int,
    total: int,
    expected: int,
) -> None:
    """AC (ticket #13): the exit code reflects partial success."""
    links = [f"https://example.com/{chr(ord('a') + i)}" for i in range(total)]
    install_resolver(
        monkeypatch,
        {
            link: {**BASIC_DOWNLOAD, "video_id": f"id{i}"}
            for i, link in enumerate(links)
        },
    )
    failing = set(links[:failing_count])

    def fail_first_n(plan, progress):
        if plan.url in failing:
            raise UserError("video removed by uploader")
        return _writing_executor(plan, progress)

    install_executor(monkeypatch, fail_first_n)

    code = main(["--folder", str(tmp_path), *links])
    assert code == expected


# ---------------------------------------------------------------------------
# AC: interrupted at any time -- completed Items stay intact
# ---------------------------------------------------------------------------


def test_an_interrupt_during_the_first_item_names_what_was_never_run(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An interrupt before any Item finished still reports honestly.

    Nothing succeeded; the in-flight Item is named as interrupted and
    the rest as not run; the exit is the conventional 130.
    """
    links = [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    install_resolver(monkeypatch, {link: BASIC_DOWNLOAD for link in links})

    def interrupting_executor(plan, progress):
        raise KeyboardInterrupt("simulated Ctrl-C")

    install_executor(monkeypatch, interrupting_executor)

    code = main(["--folder", str(tmp_path), *links])

    captured = capsys.readouterr()
    assert code == 130
    err = captured.err
    assert "0 ok, 1 failed." in err
    assert "Interrupted: 2 Item(s) were not run." in err
    # No completed Item left a file behind, because none completed --
    # but nothing was corrupted either.
    assert (tmp_path / "Some Channel").exists() is False


def test_an_interrupt_during_planning_is_handled_the_same_way(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The interrupt can arrive while a link is being planned.

    The resolver raises ``KeyboardInterrupt`` mid-Queue. The Queue
    still stops cleanly with exit 130, and the completed Item from
    earlier in the run is untouched.
    """

    def interrupting_resolver(url: str) -> dict[str, object]:
        if url == "https://example.com/b":
            raise KeyboardInterrupt("simulated Ctrl-C")
        if url == "https://example.com/a":
            return dict(BASIC_DOWNLOAD)
        raise UserError(f"unexpected link in test: {url}")  # pragma: no cover

    install_resolver(
        monkeypatch,
        {
            "https://example.com/a": BASIC_DOWNLOAD,
            "https://example.com/b": BASIC_DOWNLOAD,
        },
    )
    # Wrap the installed fake resolver with the interrupting one.
    monkeypatch.setattr("vicxiun.cli._resolver", interrupting_resolver)
    install_executor(monkeypatch, _writing_executor)

    code = main(
        [
            "--folder",
            str(tmp_path),
            "https://example.com/a",
            "https://example.com/b",
        ]
    )

    err = capsys.readouterr().err
    assert code == 130
    assert "ok: https://example.com/a" in err
    # Item a's completed file is on disk and untouched.
    files = list((tmp_path / "Some Channel").iterdir())
    assert len(files) == 1
