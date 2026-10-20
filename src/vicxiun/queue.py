"""Queue: strictly sequential handling of a Job's Items (ticket #13).

The Queue is the loop that turns a list of links into one finished Job.
It is deliberately simple and deliberately sequential:

* One Item at a time, in the order the user typed the links. No
  concurrency -- a waiting Capture blocks the Queue, and that is
  documented in the Milestone 1 spec (issue #1).
* A failed Item does not stop the rest. The Queue continues past any
  failure, which is the whole point of a session with many links.
* Everything that happens is recorded. At the end the caller prints a
  summary listing what succeeded, what failed, and why, so the user is
  never left guessing about what landed on disk.
* An interrupt stops the Queue at once. Items that already finished
  stay finished -- their files are complete and untouched -- and the
  remaining Items are named as not run. The Job exits with the
  conventional 130 code so scripts can tell interruption from failure.

The engine seam is injected (``resolver`` and ``executor`` callables),
so no test for the Queue itself reaches the network. The CLI wires the
real engine in; tests substitute fakes.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from vicxiun.engine import default_progress_printer
from vicxiun.errors import (
    EXIT_CODE,
    PartialSuccess,
    PrerequisiteMissing,
    RefusedContent,
    UnexpectedFailure,
    UserError,
)
from vicxiun.plan import InvalidLinkError, Plan, Profile, resolve_plan

ResolverCallable = Callable[[str], dict[str, object]]
ExecutorCallable = Callable[[Plan, Callable[[dict[str, object]], None] | None], Path]


# ---------------------------------------------------------------------------
# Per-Item outcomes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemResult:
    """The outcome of one Item in the Queue.

    A successful Item carries no error; every failure carries an
    instance of one of the typed error classes in ``vicxiun.errors``
    (or ``KeyboardInterrupt`` when the run was interrupted while this
    Item was in progress). That makes the exit-code mapping a single
    ``EXIT_CODE`` lookup on the most-specific exception, instead of a
    string-keyed table.
    """

    link: str
    path: Path | None
    error: BaseException | None


_OUTCOME_LABELS: dict[type[BaseException], str] = {
    KeyboardInterrupt: "interrupted",
    RefusedContent: "refused",
    PrerequisiteMissing: "missing prereq",
    UserError: "user error",
    UnexpectedFailure: "unexpected",
}


def label_for(error: BaseException) -> str:
    """Human-readable label for a named error."""
    for cls in type(error).__mro__:
        label = _OUTCOME_LABELS.get(cls)
        if label is not None:
            return label
    return "error"


def _classify_failure(exc: BaseException) -> BaseException:
    """Map a raised exception to the closest named error.

    The engine seam already translates ``yt_dlp.DownloadError`` to a
    named error; the Queue just normalises anything else to
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


def _render_one_line(link: str, outcome: str, detail: str) -> str:
    """Render the single line that describes one Item's result."""
    return f"{link}: {outcome} ({detail})\n"


def _record_failure(result: ItemResult) -> None:
    """Print the per-Item failure line for ``result``."""
    assert result.error is not None
    label = label_for(result.error)
    print(
        _render_one_line(result.link, label, str(result.error)).rstrip(),
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# One Item
# ---------------------------------------------------------------------------


def run_item(
    link: str,
    profile: Profile,
    output_root: Path,
    resolver: ResolverCallable,
    executor: ExecutorCallable,
    wait_for_stream: bool = False,
) -> ItemResult:
    """Run a single Item; return its outcome.

    Plan lines and the per-Item one-line result are printed on stderr.
    The caller aggregates ``ItemResult`` values into a summary.

    ``KeyboardInterrupt`` is ``BaseException``, not ``Exception``, so
    the broad ``except Exception`` below does not swallow it -- the
    interrupt escapes to the Queue, which stops cleanly.
    """
    try:
        plan = resolve_plan(link, profile, output_root, resolver, wait_for_stream)
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
        actual = executor(plan, progress)
    except Exception as exc:  # noqa: BLE001 - boundary, mapped to 5
        named = _classify_failure(exc)
        result = ItemResult(link=link, path=None, error=named)
        _record_failure(result)
        return result

    result = ItemResult(link=link, path=actual, error=None)
    print(_render_one_line(link, "ok", str(actual)).rstrip(), file=sys.stderr)
    return result


# ---------------------------------------------------------------------------
# The Queue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueueOutcome:
    """Everything the caller needs after a Queue has run.

    ``results`` holds one ``ItemResult`` per Item that was *started*
    (an interrupted in-progress Item is recorded as interrupted).
    ``interrupted`` is True only when the run was stopped by an
    interrupt; the never-started Items are simply the links minus
    ``results`` and are named in the summary.
    """

    links: tuple[str, ...]
    results: list[ItemResult]
    interrupted: bool = False

    @property
    def successes(self) -> list[ItemResult]:
        return [r for r in self.results if r.error is None]

    @property
    def failures(self) -> list[ItemResult]:
        return [r for r in self.results if r.error is not None]

    def summary(self) -> str:
        """Render the closing summary: what succeeded, failed, and why.

        One line per Item, then the counts. When the run was
        interrupted the remaining Items are named as not run.
        """
        lines: list[str] = []
        for r in self.results:
            if r.error is None:
                assert r.path is not None
                lines.append(f"ok: {r.link} ({r.path})")
            else:
                lines.append(f"failed: {r.link} ({label_for(r.error)}: {r.error})")
        if self.interrupted:
            not_started = len(self.links) - len(self.results)
            lines.append(f"Interrupted: {not_started} Item(s) were not run.")
        failures = self.failures
        lines.append(f"{len(self.successes)} ok, {len(failures)} failed.")
        return "\n".join(lines) + "\n"

    def exit_code(self) -> int:
        """The exit code for the whole Job (the ``main()`` contract).

        * 0 -- every Item succeeded.
        * 1 -- partial success: some Items succeeded, some failed.
        * 130 -- the run was interrupted; completed Items are untouched.
        * otherwise -- every Item failed, and the most-specific named
          cause's code is returned.
        """
        if self.interrupted:
            return EXIT_CODE[KeyboardInterrupt]
        failures = self.failures
        if not failures:
            return 0
        if self.successes:
            return EXIT_CODE[PartialSuccess]
        return EXIT_CODE[_deepest_named(failures)]


def run_queue(
    links: list[str],
    *,
    profile: Profile,
    output_root: Path,
    resolver: ResolverCallable,
    executor: ExecutorCallable,
    wait_for_stream: bool = False,
) -> QueueOutcome:
    """Handle every Item in ``links`` strictly sequentially.

    Items run one at a time, in the order the user typed them. A
    failed Item is recorded and the Queue continues to the next one.
    A ``KeyboardInterrupt`` stops the Queue: the Item in flight is
    recorded as interrupted, the rest are marked not run, and the
    Items that already finished are untouched.
    """
    results: list[ItemResult] = []
    interrupted = False
    for index, link in enumerate(links):
        try:
            results.append(
                run_item(
                    link,
                    profile,
                    output_root,
                    resolver,
                    executor,
                    wait_for_stream=wait_for_stream,
                )
            )
        except KeyboardInterrupt:
            # Stop at once. The Item in flight is recorded as
            # interrupted; nothing after it starts. Items that already
            # finished keep their completed files and stay reported.
            interrupted = True
            results.append(
                ItemResult(
                    link=link,
                    path=None,
                    error=KeyboardInterrupt(
                        "interrupted before this Item finished",
                    ),
                )
            )
            break
    return QueueOutcome(
        links=tuple(links),
        results=results,
        interrupted=interrupted,
    )


__all__ = [
    "ItemResult",
    "QueueOutcome",
    "label_for",
    "run_item",
    "run_queue",
]
