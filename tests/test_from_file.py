"""Link-file tests (ticket #14).

The acceptance criteria under test:

* A text file of links, one per line, is accepted and processed in
  order.
* Blank lines and commented lines are ignored.

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


def _meta(link: str) -> dict[str, object]:
    """Metadata for ``link`` with a distinct id, so targets never collide."""
    return {**BASIC_DOWNLOAD, "video_id": link[-1]}


def _writing_executor(plan, progress):
    """A fake executor that writes the file the Plan promised."""
    plan.target_path.parent.mkdir(parents=True, exist_ok=True)
    plan.target_path.write_bytes(b"x")
    return plan.target_path


def _install_queue(monkeypatch, mapping, ran):
    """Install a resolver for ``mapping`` and an executor that records."""
    install_resolver(monkeypatch, mapping)

    def recording_executor(plan, progress):
        ran.append(plan.url)
        return _writing_executor(plan, progress)

    install_executor(monkeypatch, recording_executor)


# ---------------------------------------------------------------------------
# AC: a text file of links is accepted and processed in order
# ---------------------------------------------------------------------------


def test_a_file_of_links_is_processed_in_order(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC (ticket #14): the file's links are handled one by one, in order."""
    file = tmp_path / "links.txt"
    file.write_text(
        "https://example.com/a\nhttps://example.com/b\nhttps://example.com/c\n",
        encoding="utf-8",
    )
    links = [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    ran: list[str] = []
    _install_queue(monkeypatch, {link: _meta(link) for link in links}, ran)

    code = main(["--folder", str(tmp_path), "--from-file", str(file)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert ran == links


def test_command_line_links_come_first_then_the_file(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Links typed on the command line run first, then the file's lines."""
    file = tmp_path / "links.txt"
    file.write_text("https://example.com/b\nhttps://example.com/c\n", encoding="utf-8")
    ran: list[str] = []
    _install_queue(
        monkeypatch,
        {
            link: _meta(link)
            for link in (
                "https://example.com/a",
                "https://example.com/b",
                "https://example.com/c",
            )
        },
        ran,
    )

    code = main(
        [
            "--folder",
            str(tmp_path),
            "--from-file",
            str(file),
            "https://example.com/a",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert ran == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]


# ---------------------------------------------------------------------------
# AC: blank lines and commented lines are ignored
# ---------------------------------------------------------------------------


def test_blank_and_commented_lines_are_ignored(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC (ticket #14): notes in the list never reach the Queue.

    The resolver knows only links a and b; a blank or commented line
    that slipped through would fail resolution and the run would not
    exit 0.
    """
    file = tmp_path / "links.txt"
    file.write_text(
        "# prepared before the weekend\n"
        "\n"
        "   \n"
        "https://example.com/a\n"
        "  # indented comment\n"
        "https://example.com/b\n"
        "\n",
        encoding="utf-8",
    )
    ran: list[str] = []
    _install_queue(
        monkeypatch,
        {
            "https://example.com/a": _meta("https://example.com/a"),
            "https://example.com/b": _meta("https://example.com/b"),
        },
        ran,
    )

    code = main(["--folder", str(tmp_path), "--from-file", str(file)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert ran == ["https://example.com/a", "https://example.com/b"]


def test_a_hash_inside_a_link_is_kept(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A '#' after the start of the line is part of the link, not a note."""
    file = tmp_path / "links.txt"
    file.write_text("https://example.com/watch?v=a#fragment\n", encoding="utf-8")
    ran: list[str] = []
    _install_queue(
        monkeypatch,
        {"https://example.com/watch?v=a#fragment": BASIC_DOWNLOAD},
        ran,
    )

    code = main(["--folder", str(tmp_path), "--from-file", str(file)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert ran == ["https://example.com/watch?v=a#fragment"]


# ---------------------------------------------------------------------------
# File errors: named, exit 2, nothing runs
# ---------------------------------------------------------------------------


def test_a_missing_file_is_a_user_error(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file that does not exist is a user error, before anything runs."""
    ran: list[str] = []
    _install_queue(monkeypatch, {}, ran)

    code = main(["--folder", str(tmp_path), "--from-file", str(tmp_path / "nope.txt")])

    captured = capsys.readouterr()
    assert code == 2
    assert "nope.txt" in captured.err
    assert ran == []
    assert (tmp_path / "Some Channel").exists() is False


def test_an_all_comment_file_with_no_links_is_a_user_error(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file of only notes and blanks supplies no links: user error."""
    file = tmp_path / "links.txt"
    file.write_text("# nothing yet\n\n   \n", encoding="utf-8")
    ran: list[str] = []
    _install_queue(monkeypatch, {}, ran)

    with pytest.raises(SystemExit) as exit_info:
        main(["--folder", str(tmp_path), "--from-file", str(file)])

    assert exit_info.value.code == 2
    assert "at least one LINK is required" in capsys.readouterr().err
    assert ran == []


def test_a_utf8_bom_is_tolerated(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Windows editors add a BOM; the first link must survive it."""
    file = tmp_path / "links.txt"
    file.write_text("https://example.com/a\n", encoding="utf-8-sig")
    ran: list[str] = []
    _install_queue(monkeypatch, {"https://example.com/a": BASIC_DOWNLOAD}, ran)

    code = main(["--folder", str(tmp_path), "--from-file", str(file)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert ran == ["https://example.com/a"]
