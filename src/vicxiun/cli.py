"""The ``vicxiun`` command line.

This module is deliberately thin. It owns argument parsing, the process
exit code, and the per-Item progress printer. The download and capture
logic is reached through the engine seam (``vicxiun.engine``); the CLI's
job is to validate, dispatch and report.

Ticket #8 wires the real download/capture paths through ``--execute``;
``--dry-run`` continues to print Plans without downloading.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path

from vicxiun import __version__
from vicxiun.engine import (
    Engine,
    ExecutorCallable,
    ResolverCallable,
    YtDlpEngine,
    build_default_engine,
    default_progress_printer,
)
from vicxiun.errors import (
    PartialSuccess,
    PrerequisiteMissing,
    RefusedContent,
    UnexpectedFailure,
    UserError,
    exit_code_for,
)
from vicxiun.plan import (
    InvalidLinkError,
    Plan,
    profile_from_name,
    render_summary,
    resolve_plan,
)
from vicxiun.prereqs import run as run_prereq_check

PROG = "vicxiun"

# Module-level swappable seams for the metadata resolver and the executor.
# Tests install fakes here so the CLI exercise stays offline. The
# executor receives a Plan and an optional progress callback.
_resolver: ResolverCallable | None = None
_executor: ExecutorCallable | None = None


def set_resolver(resolver: ResolverCallable) -> None:
    """Replace the resolver used by ``main`` (test seam)."""
    global _resolver
    _resolver = resolver


def set_executor(executor: ExecutorCallable) -> None:
    """Replace the executor used by ``main`` (test seam)."""
    global _executor
    _executor = executor


def _build_engine() -> Engine:
    """Return the engine the real run uses; honours any test seams."""
    return YtDlpEngine(
        resolver=_resolver,
        executor=_executor,
    )


def _resolve_metadata(url: str) -> dict[str, object]:
    """Metadata-only fetch. Falls back to the real engine in production."""
    if _resolver is not None:
        return _resolver(url)
    return build_default_engine().resolve_metadata(url)


def _execute_plan(
    plan: Plan,
    on_progress: Callable[[dict[str, object]], None] | None,
) -> Path:
    """Run a Plan. Falls back to the real engine in production."""
    if _executor is not None:
        return _executor(plan, on_progress)
    return build_default_engine().execute(plan, on_progress)


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


def _render_one_line(link: str, outcome: str, detail: str) -> str:
    """Render the single line that describes one Item's result.

    The line is the only output the real run emits per Item: it is
    written to stdout so it composes with the closing summary.
    """
    return f"{link}: {outcome} ({detail})\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command described by ``argv`` and return the exit code.

    Exit codes follow the project contract:

    * 0 — every Item downloaded or captured.
    * 1 — some Items succeeded, some failed (partial success).
    * 2 — user error (bad arguments, bad link, bad path).
    * 3 — a Prerequisite program is missing.
    * 4 — a refused Item (DRM or paywall).
    * 5 — unexpected failure (anything else that bubbles up).

    The Prerequisite check still runs even with a link, so a missing
    ffmpeg is announced before anything else. ``--version`` and
    ``--help`` are handled by argparse before the check fires.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    code = run_prereq_check()
    if code != 0:
        return code

    if not args.links:
        parser.error("at least one LINK is required")

    profile = profile_from_name(args.profile)
    output_root = (
        Path(args.folder).expanduser() if args.folder else default_output_root()
    )

    if args.dry_run:
        return _run_dry_run(parser, args, profile, output_root)

    return _run_real(parser, args, profile, output_root)


# ---------------------------------------------------------------------------
# Dry-run path (preserved from #5/#6; unchanged behaviour for --dry-run)
# ---------------------------------------------------------------------------


def _run_dry_run(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    profile,
    output_root: Path,
) -> int:
    """Resolve each Item to a Plan, print it, and stop.

    Behaviour is the same as before ticket #8 -- only the real run
    path is new.
    """
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
        return 1
    if failures:
        return 2
    return 0


# ---------------------------------------------------------------------------
# Real run path (ticket #8)
# ---------------------------------------------------------------------------


def _run_real(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    profile,
    output_root: Path,
) -> int:
    """Execute each Item for real and report results.

    Prints progress while each Item runs, and one final line per Item
    describing what happened. The exit code follows the contract in
    ``main()``: 0 if every Item succeeded, 1 for partial success,
    otherwise the named code for whatever failed.
    """
    successes: list[tuple[str, Path]] = []
    failures: list[tuple[str, str, BaseException]] = []
    refused = False
    prereq_missing = False

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
            failures.append((link, "user error", exc))
            print(str(exc), file=sys.stderr)
            continue

        # Announce what we are about to do. This is the user-visible
        # contract: a line that names the link, what kind of Item it is,
        # and where the file will land.
        print(
            f"{link}: {plan.kind.name} with profile {plan.profile.name}",
            file=sys.stderr,
        )
        print(f"  target: {plan.target_path}", file=sys.stderr)

        progress = default_progress_printer()
        try:
            actual = _execute_plan(plan, progress)
        except RefusedContent as exc:
            refused = True
            failures.append((link, "refused", exc))
            print(_render_one_line(link, "refused", str(exc)).rstrip(),
                  file=sys.stderr)
            continue
        except PrerequisiteMissing as exc:
            prereq_missing = True
            failures.append((link, "missing prerequisite", exc))
            print(_render_one_line(link, "missing prereq", str(exc)).rstrip(),
                  file=sys.stderr)
            continue
        except UserError as exc:
            failures.append((link, "user error", exc))
            print(_render_one_line(link, "user error", str(exc)).rstrip(),
                  file=sys.stderr)
            continue
        except Exception as exc:  # noqa: BLE001 - boundary, mapped to 5
            unexpected = UnexpectedFailure(str(exc))
            failures.append((link, "unexpected", unexpected))
            print(_render_one_line(link, "unexpected", str(exc)).rstrip(),
                  file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            continue

        successes.append((link, actual))
        print(
            _render_one_line(link, "ok", str(actual)).rstrip(),
            file=sys.stderr,
        )

    # The one-line summary at the end is on stdout, so it composes with
    # ``--dry-run``'s closing line for consistency.
    print(_summary(successes, failures), end="")

    if not failures:
        return 0
    if successes:
        return exit_code_for(PartialSuccess(f"{len(successes)} succeeded, {len(failures)} failed"))
    # All Items failed: pick the most-specific code.
    if refused and not prereq_missing and all(c == "refused" for _, c, _ in failures):
        return exit_code_for(RefusedContent())
    if prereq_missing and all(c == "missing prerequisite" for _, c, _ in failures):
        return exit_code_for(PrerequisiteMissing())
    if all(c == "user error" for _, c, _ in failures):
        return exit_code_for(UserError())
    return exit_code_for(UnexpectedFailure())


def _summary(
    successes: list[tuple[str, Path]],
    failures: list[tuple[str, str, BaseException]],
) -> str:
    """Render the closing line that summarises the whole run."""
    ok = len(successes)
    bad = len(failures)
    if not failures:
        return f"{ok} ok, 0 failed.\n"
    return f"{ok} ok, {bad} failed.\n"