"""The ``vicxiun`` command line.

This module is deliberately thin. It owns argument parsing, the process
exit code, and the per-Item progress printer. The download and capture
logic is reached through the engine seam (``vicxiun.engine``); the CLI's
job is to validate, dispatch and report.

Ticket #8 wires the real download paths through ``main``; ``--dry-run``
continues to print Plans without downloading.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from vicxiun import __version__
from vicxiun.engine import (
    ExecutorCallable,
    ResolverCallable,
    build_default_engine,
    default_progress_printer,
)
from vicxiun.errors import (
    EXIT_CODE,
    PartialSuccess,
    PrerequisiteMissing,
    RefusedContent,
    UnexpectedFailure,
    UserError,
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
_prereq_check: Callable[[], int] | None = None


def set_resolver(resolver: ResolverCallable) -> None:
    """Replace the resolver used by ``main`` (test seam)."""
    global _resolver
    _resolver = resolver


def set_executor(executor: ExecutorCallable) -> None:
    """Replace the executor used by ``main`` (test seam)."""
    global _executor
    _executor = executor


def set_prereq_check(prereq_check: Callable[[], int]) -> None:
    """Replace the Prereq check used by ``main`` (test seam).

    Tests that exercise business logic through the CLI install a
    no-op here so they do not depend on ffmpeg / node being on the
    test machine's ``PATH``. The prereq check itself is tested
    separately under ``tests/test_prereqs.py``.
    """
    global _prereq_check
    _prereq_check = prereq_check


def _run_prereq_check() -> int:
    """Honour the test seam if one is installed; else run the real check."""
    if _prereq_check is not None:
        return _prereq_check()
    return run_prereq_check()


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

    The line is the only output the real run emits per Item, written
    to stderr alongside progress and the closing summary, so a single
    coherent stream carries everything the user sees while the run
    happens.
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

    code = _run_prereq_check()
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


@dataclass(frozen=True)
class ItemResult:
    """The outcome of one Item in the real run.

    A successful Item carries no error; every failure carries an
    instance of one of the typed error classes in ``vicxiun.errors``.
    That makes the exit-code mapping a single ``EXIT_CODE`` lookup on
    the most-specific exception, instead of a string-keyed table.
    """

    link: str
    path: Path | None
    error: BaseException | None


_OUTCOME_LABELS: dict[type[BaseException], str] = {
    RefusedContent: "refused",
    PrerequisiteMissing: "missing prereq",
    UserError: "user error",
    UnexpectedFailure: "unexpected",
}


def _label_for(error: BaseException) -> str:
    """Human-readable label for a named error."""
    for cls in type(error).__mro__:
        label = _OUTCOME_LABELS.get(cls)
        if label is not None:
            return label
    return "error"


def _record_failure(result: ItemResult) -> None:
    """Print the per-Item failure line for ``result``."""
    assert result.error is not None
    label = _label_for(result.error)
    print(
        _render_one_line(result.link, label, str(result.error)).rstrip(),
        file=sys.stderr,
    )


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
    results: list[ItemResult] = []
    for link in args.links:
        results.append(_run_one(link, profile, output_root, args.wait_for_stream))

    successes = [r for r in results if r.error is None]
    failures = [r for r in results if r.error is not None]

    # The one-line summary is on stderr, alongside the per-Item
    # result lines and the progress lines, so a single coherent stream
    # carries everything the user sees while the run happens.
    print(_summary(successes, failures), end="", file=sys.stderr)

    if not failures:
        return 0
    if successes:
        return EXIT_CODE[PartialSuccess]
    # All Items failed: the most-specific exception's exit code. We pick
    # the most specific by walking the MROs and choosing the one whose
    # class is a subclass of the others (the deepest named error).
    deepest = _deepest_named(failures)
    return EXIT_CODE[deepest]


def _run_one(
    link: str,
    profile,
    output_root: Path,
    wait_for_stream: bool,
) -> ItemResult:
    """Run a single Item; return its outcome.

    Progress lines and the per-Item one-line result are printed on
    stderr. The caller aggregates ``ItemResult`` values into a summary.
    """
    try:
        plan = resolve_plan(
            link,
            profile,
            output_root,
            _resolve_metadata,
            wait_for_stream=wait_for_stream,
        )
    except (InvalidLinkError, UserError) as exc:
        print(str(exc), file=sys.stderr)
        return ItemResult(link=link, path=None, error=exc)

    print(
        f"{link}: {plan.kind.name} with profile {plan.profile.name}",
        file=sys.stderr,
    )
    print(f"  target: {plan.target_path}", file=sys.stderr)

    progress = default_progress_printer()
    try:
        actual = _execute_plan(plan, progress)
    except Exception as exc:  # noqa: BLE001 - boundary, mapped to 5
        # KeyboardInterrupt and SystemExit are BaseException, not
        # Exception, so this branch does not swallow them. A user
        # Ctrl-C still produces the conventional 130 exit.
        named = _classify_failure(exc)
        result = ItemResult(link=link, path=None, error=named)
        _record_failure(result)
        return result

    result = ItemResult(link=link, path=actual, error=None)
    print(_render_one_line(link, "ok", str(actual)).rstrip(), file=sys.stderr)
    return result


def _classify_failure(exc: BaseException) -> BaseException:
    """Map a raised exception to the closest named error.

    The engine seam already translates ``yt_dlp.DownloadError`` to a
    named error; the CLI just normalises anything else to
    ``UnexpectedFailure`` so the exit-code contract holds.
    """
    for cls in (RefusedContent, PrerequisiteMissing, UserError):
        if isinstance(exc, cls):
            return exc
    return UnexpectedFailure(str(exc))


def _deepest_named(failures: list[ItemResult]) -> type[BaseException]:
    """Return the most-specific named-error class across ``failures``.

    Used to pick an exit code when every Item failed: we honour the
    most-specific cause rather than the order in which Items ran.
    """
    candidates: list[type[BaseException]] = []
    for r in failures:
        assert r.error is not None
        candidates.append(type(r.error))
    # Pick the deepest in the class hierarchy.
    best = candidates[0]
    for cls in candidates[1:]:
        if issubclass(cls, best):
            best = cls
    return best


def _summary(
    successes: list[ItemResult],
    failures: list[ItemResult],
) -> str:
    """Render the closing line that summarises the whole run."""
    ok = len(successes)
    bad = len(failures)
    if not failures:
        return f"{ok} ok, 0 failed.\n"
    return f"{ok} ok, {bad} failed.\n"