"""Tests for the ``vicxiun`` command itself.

The command a user types is this project's integration seam: arguments in,
text and exit codes out. Later tickets exercise the download and capture
commands through this same seam.
"""

from __future__ import annotations

import re

import pytest

from vicxiun import __version__
from vicxiun import cli as cli_mod
from vicxiun.cli import main


@pytest.mark.parametrize(
    "argv",
    [
        ["--dry-run", "https://example.com/video"],
        ["--wait-for-stream", "https://example.com/video"],
        ["--wait-interval", "15", "https://example.com/video"],
        ["--profile", "best", "https://example.com/video"],
        ["--folder", "downloads", "https://example.com/video"],
        ["--from-file", "links.txt"],
        ["--no-skip", "https://example.com/video"],
        ["--playlist-limit", "0", "https://example.com/video"],
    ],
)
def test_documented_full_options_parse(argv: list[str]) -> None:
    """Disabling abbreviations must retain every documented full option."""
    cli_mod.build_parser().parse_args(argv)


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


@pytest.mark.parametrize(
    ("argv", "option"),
    [
        (["--playlist", "1", "https://example.com/video"], "--playlist"),
        (["--wait"], "--wait"),
        (["--no-s"], "--no-s"),
        (["--dr"], "--dr"),
    ],
)
def test_prefix_options_are_rejected_before_any_work_runs(
    argv: list[str],
    option: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A prefix must not silently select a documented long option."""
    prereq_checks: list[None] = []
    monkeypatch.setattr(
        cli_mod,
        "_prereq_check",
        lambda: prereq_checks.append(None) or 0,
    )
    monkeypatch.setattr(
        cli_mod,
        "_resolver",
        lambda url: pytest.fail(f"parser accepted typo and resolved {url}"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_executor",
        lambda plan, progress: pytest.fail("parser accepted typo and executed work"),
    )

    with pytest.raises(SystemExit) as exit_info:
        main(argv)

    assert exit_info.value.code == 2
    assert f"unrecognized arguments: {option}" in capsys.readouterr().err
    assert prereq_checks == []
