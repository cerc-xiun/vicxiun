"""Tests for the ``vicxiun`` command itself.

The command a user types is this project's integration seam: arguments in,
text and exit codes out. Later tickets exercise the download and capture
commands through this same seam.
"""

from __future__ import annotations

import re

import pytest

from vicxiun import __version__
from vicxiun.cli import main


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
