"""Tests for the installed entry points.

These run the real ``vicxiun`` console script and ``python -m vicxiun`` in a
subprocess, so a missing or misdeclared entry point fails the suite even
when the argument parsing itself is fine.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from vicxiun import __version__

EXPECTED_VERSION_OUTPUT = f"vicxiun {__version__}\n"


def run_command(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def console_script() -> Path:
    """Locate the installed ``vicxiun`` script without relying on PATH."""
    scripts_directory = Path(sys.executable).parent
    for name in ("vicxiun.exe", "vicxiun"):
        candidate = scripts_directory / name
        if candidate.is_file():
            return candidate

    found = shutil.which("vicxiun")
    if found is not None:
        return Path(found)

    pytest.fail(
        f"no vicxiun console script next to {sys.executable} and none on PATH; "
        "run `uv sync` first",
        pytrace=False,
    )


def test_console_script_prints_the_version() -> None:
    result = run_command(str(console_script()), "--version")

    assert result.returncode == 0, result.stderr
    assert result.stdout == EXPECTED_VERSION_OUTPUT


def test_console_script_prints_help() -> None:
    result = run_command(str(console_script()), "--help")

    assert result.returncode == 0, result.stderr
    assert "--version" in result.stdout


def test_python_dash_m_prints_the_version() -> None:
    result = run_command(sys.executable, "-m", "vicxiun", "--version")

    assert result.returncode == 0, result.stderr
    assert result.stdout == EXPECTED_VERSION_OUTPUT
