"""Tests for the ``vicxiun`` command itself.

The command a user types is this project's integration seam: arguments in,
text and exit codes out. Later tickets exercise the download and capture
commands through this same seam.
"""

from __future__ import annotations

import re

import pytest

from vicxiun import __version__
from vicxiun.cli import build_parser, main


def test_version_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"vicxiun {__version__}\n"
    assert re.fullmatch(r"\d+\.\d+\.\d+\S*", __version__), __version__


def test_help_lists_the_available_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    printed = capsys.readouterr().out
    assert printed.startswith("usage: vicxiun")
    assert "--version" in printed
    assert "-h, --help" in printed


def test_unknown_option_is_a_user_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--definitely-not-an-option"])

    assert exit_info.value.code == 2
    assert "usage: vicxiun" in capsys.readouterr().err


def test_unknown_option_that_abbreviates_a_real_one_is_rejected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A typo must not be prefix-matched into a real flag (ticket #31).

    ``--playlist`` is not a flag; without ``allow_abbrev=False`` argparse
    reads it as ``--playlist-limit`` and the Queue runs.
    """
    with pytest.raises(SystemExit) as exit_info:
        main(["--playlist", "1", "https://example.com/watch?v=abc123"])

    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert "unrecognized arguments:" in captured.err
    assert "--playlist" in captured.err
    # Nothing ran: the Queue would have reported on stdout.
    assert captured.out == ""


def test_abbreviated_forms_of_real_flags_are_rejected() -> None:
    """The shorthand habits (``--wait``, ``--no-s``, ``--dr``) are gone."""
    parser = build_parser()
    for abbreviated in ("--wait", "--no-s", "--dr"):
        with pytest.raises(SystemExit) as exit_info:
            parser.parse_args([abbreviated, "https://example.com/watch?v=abc123"])

        assert exit_info.value.code == 2, abbreviated


def test_every_documented_flag_still_parses() -> None:
    """``allow_abbrev=False`` must not break a fully spelled flag."""
    parser = build_parser()
    parser.parse_args(
        [
            "--dry-run",
            "--wait-for-stream",
            "--wait-interval",
            "5",
            "--profile",
            "phone-safe",
            "--folder",
            "out",
            "--from-file",
            "links.txt",
            "--no-skip",
            "--playlist-limit",
            "3",
            "https://example.com/watch?v=abc123",
        ]
    )
