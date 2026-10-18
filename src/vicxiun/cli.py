"""The ``vicxiun`` command line.

This module is deliberately thin. It owns argument parsing and the process
exit code. The download and capture logic is reached through the resolver
boundary (``vicxiun.plan``); the CLI's job is to validate, dispatch and
report.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from vicxiun import __version__
from vicxiun.errors import UserError
from vicxiun.plan import (
    InvalidLinkError,
    Plan,
    profile_from_name,
    render_summary,
    resolve_plan,
)
from vicxiun.prereqs import run as run_prereq_check

PROG = "vicxiun"

# Module-level swappable seam for the metadata resolver. Tests install a
# fake here so the CLI exercise stays offline.
_resolver: Callable[[str], dict[str, object]] | None = None


def set_resolver(resolver: Callable[[str], dict[str, object]]) -> None:
    """Replace the resolver used by ``main`` (test seam)."""
    global _resolver
    _resolver = resolver


def _resolve_metadata(url: str) -> dict[str, object]:
    """Metadata-only fetch. Falls back to the real engine in production."""
    if _resolver is not None:
        return _resolver(url)
    from vicxiun.engine import build_default_engine

    return build_default_engine().resolve_metadata(url)


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the ``vicxiun`` command."""
    from vicxiun.plan import _PROFILES as PROFILES

    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Download finished videos and capture live streams.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Resolve each link into a Plan and print it; do not download. "
            "Shares the real run's code path so a Plan is genuine."
        ),
    )
    parser.add_argument(
        "--wait-for-stream",
        action="store_true",
        help=(
            "For a live stream, wait until the stream starts and then "
            "record. With --dry-run this is reported in the Plan but "
            "does not block."
        ),
    )
    parser.add_argument(
        "--profile",
        default="phone-safe",
        choices=tuple(sorted(PROFILES)),
        help=(
            "Output Profile to use. The default (phone-safe) plays on any "
            "phone without further thought; 'best' reaches for the "
            "highest quality available; 'audio' yields audio only."
        ),
    )
    parser.add_argument(
        "--folder",
        default=None,
        help=(
            "Override the output folder. Defaults to the platform default "
            "(~/Videos/vicxiun on PC, /sdcard/Movies/vicxiun on Termux). "
            "Relative paths are interpreted against the current directory."
        ),
    )
    parser.add_argument(
        "links",
        nargs="*",
        metavar="LINK",
        help="One or more http(s) links to handle.",
    )
    return parser


def default_output_root() -> Path:
    """The platform's default output folder, with no per-user setup."""
    from vicxiun.platform import default_output_root as _default

    return _default()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command described by ``argv`` and return the exit code.

    The Prerequisite check still runs (the user benefits from knowing that
    ffmpeg is missing before they try a plan), but ``--version`` and
    ``--help`` are handled by argparse before the check fires.

    ``--dry-run`` is the only command this milestone implements: it
    resolves each link into a Plan and prints it. The real download /
    capture paths land in ticket #8.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    code = run_prereq_check()
    if code != 0:
        return code

    if not args.dry_run:
        # Without --dry-run, the actual download command is #8's work.
        # Until then, refuse to silently behave like --dry-run; the user
        # would be confused about whether anything happened.
        print(
            "vicxiun: the download command is not implemented in this "
            "milestone; pass --dry-run to preview a Plan.",
            file=sys.stderr,
        )
        return 2

    if not args.links:
        # --dry-run with no links is a usage error: the user clearly
        # intended to point at something.
        parser.error("at least one LINK is required with --dry-run")

    profile = profile_from_name(args.profile)
    output_root = (
        Path(args.folder).expanduser() if args.folder else default_output_root()
    )

    plans: list[Plan] = []
    failures: list[tuple[str, BaseException]] = []
    for link in args.links:
        try:
            plan = resolve_plan(
                link,
                profile,
                output_root,
                _resolve_metadata,
                wait_for_stream=args.wait_for_stream,
            )
        except (InvalidLinkError, UserError) as exc:
            failures.append((link, exc))
            print(str(exc), file=sys.stderr)
            continue
        plans.append(plan)

    for plan in plans:
        print(plan.render(), end="")
    print(render_summary(plans), end="")

    if failures and plans:
        # Some Items were planned; some failed to validate. This matches
        # what a real run would report as partial success.
        return 1
    if failures:
        return 2
    return 0
