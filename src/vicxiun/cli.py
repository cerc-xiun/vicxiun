"""The ``vicxiun`` command line.

This module is deliberately thin. It owns argument parsing and the process
exit code, and nothing else; the download and capture commands arrive in
later tickets and are wired in here.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from vicxiun import __version__

PROG = "vicxiun"


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the ``vicxiun`` command."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Download finished videos and capture live streams.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command described by ``argv`` and return the exit code."""
    parser = build_parser()
    parser.parse_args(argv)
    return 0
