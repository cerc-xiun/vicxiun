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
* An Item the user already has is skipped, not fetched again (ticket
  #14). "Already has it" is decided before any network request: an
  exact-URL skip record beside the output names what earlier runs
  finished, and an Item whose target file is already on disk is
  skipped after resolution. Both are recorded, so re-running a
  completed Queue downloads nothing. ``skip_existing=False`` (the CLI's
  ``--no-skip``) turns the skipping off.

The engine seam is injected (``resolver`` and ``executor`` callables),
so no test for the Queue itself reaches the network. The CLI wires the
real engine in; tests substitute fakes.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from vicxiun.capture import STOP_INSTRUCTION, after_interrupt
from vicxiun.diskspace import RUNNING_FLOOR_BYTES, SpaceWatch, capture_pre_flight
from vicxiun.engine import default_progress_printer
from vicxiun.errors import (
    EXIT_CODE,
    PartialSuccess,
    PrerequisiteMissing,
    RefusedContent,
    UnexpectedFailure,
    UserError,
)
from vicxiun.plan import (
    CaptureKind,
    InvalidLinkError,
    Plan,
    Profile,
    resolve_plan,
)

ResolverCallable = Callable[[str], dict[str, object]]
ExecutorCallable = Callable[[Plan, Callable[[dict[str, object]], None] | None], Path]

# How often a waiting Capture re-checks whether the stream has started
# (ticket #11). Seconds; overridable with ``--wait-interval``.
DEFAULT_WAIT_INTERVAL = 60

# A playlist with more Items than this asks for confirmation before it
# starts (ticket #15). Overridable with ``--playlist-limit``.
DEFAULT_PLAYLIST_LIMIT = 50

# The confirmation callback for a very large playlist (ticket #15):
# given the count of Items found, it answers whether the run may start.
ConfirmCallable = Callable[[int], bool]

# The skip record lives alongside the output (ticket #14): a plain
# text file in the output root, one URL per line.
SKIP_RECORD_NAME = ".vicxiun-done.txt"


class SkipRecord:
    """Which Items are already done, kept beside the output (ticket #14).

    A plain text file (``SKIP_RECORD_NAME`` in the output root), one
    URL per line, in the order they were finished. The match against a
    link is the exact string the user typed, which is what makes the
    skip decision free: no resolve, no network, before any Item work.
    The record is not a download history -- it holds only what is done,
    is rewritten in place, and carries no timestamps.
    """

    def __init__(self, path: Path, urls: tuple[str, ...] = ()) -> None:
        self.path = path
        self._urls = list(urls)

    @classmethod
    def load(cls, path: Path) -> tuple[SkipRecord, str | None]:
        """Read the record at ``path``; return ``(record, warning)``.

        A missing file is the ordinary first-run case: an empty record,
        no warning. A corrupt or unreadable one is named in a warning
        and skipped over, so a hand-mangled record cannot stop a run --
        the built-in behaviour (fetch everything) is the safe fallback.
        """
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls(path), None
        except (OSError, ValueError) as exc:
            return cls(path), f"Ignoring unreadable skip record {path}: {exc}"
        urls: list[str] = []
        for line in raw.splitlines():
            url = line.strip()
            if url and url not in urls:
                urls.append(url)
        return cls(path, tuple(urls)), None

    def __contains__(self, url: str) -> bool:
        return url in self._urls

    def add(self, url: str) -> None:
        """Note ``url`` as done, keeping first-seen order, no duplicates."""
        if url not in self._urls:
            self._urls.append(url)

    def save(self) -> None:
        """Rewrite the record in place.

        Called after every Item that finished (or was skipped), so an
        interrupt loses nothing that completed. Best-effort on errors:
        the warning is printed and the run continues, because a record
        that could not be written must not fail a download that did.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                "".join(f"{url}\n" for url in self._urls), encoding="utf-8"
            )
        except OSError as exc:
            print(
                f"warning: could not write the skip record {self.path}: {exc}",
                file=sys.stderr,
            )


def _is_waiting_capture(plan: Plan) -> bool:
    """True when ``plan`` is a Capture whose stream has not started.

    A scheduled stream resolves as ``CaptureKind`` with ``is_live``
    False (ticket #11); this is the one place that shape is decided.
    """
    return isinstance(plan.kind, CaptureKind) and not plan.kind.is_live


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

    A skipped Item (ticket #14) carries no error either: it counts as
    done, not failed. ``path`` says where the file already is when the
    skip was decided by file presence, and stays ``None`` when the
    skip came from the record (whose check runs before any resolve).
    A declined Item (ticket #15) carries no error either: the user
    said no to a very large playlist, so nothing was downloaded and
    the Queue stops cleanly.
    """

    link: str
    path: Path | None
    error: BaseException | None
    skipped: bool = False
    declined: bool = False


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


def _exit_code_for_class(cls: type[BaseException]) -> int:
    """The exit code for a named-error class, by its nearest named ancestor.

    A failure can carry a named-error subclass that is not itself a key
    in ``EXIT_CODE`` (``MetadataError`` is a ``UserError``, for
    example); the MRO decides, and anything truly unnamed is treated as
    an unexpected failure.
    """
    for ancestor in cls.__mro__:
        code = EXIT_CODE.get(ancestor)
        if code is not None:
            return code
    return EXIT_CODE[UnexpectedFailure]


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
# Waiting for a scheduled stream (ticket #11)
# ---------------------------------------------------------------------------


def _wait_for_stream_start(
    plan: Plan,
    profile: Profile,
    output_root: Path,
    resolver: ResolverCallable,
    interval: int,
) -> Plan:
    """Block until the planned stream has started; return the Plan to run.

    The Queue is strictly sequential, so a waiting Item blocks the rest
    of the Queue; the caller has already announced that. Every
    ``interval`` seconds the link is re-resolved: while it still comes
    back as a scheduled Capture the wait continues (one visible line
    per check), and a check that *fails* is also printed and retried --
    the user has walked away, so one network hiccup must not lose the
    recording. The wait ends as soon as the link resolves to anything
    else: a live Capture (the normal case -- the engine records it from
    the beginning via ``live_from_start``), or a Download, which means
    the stream ended and finished before a check could see it live, in
    which case the platform's archive is the recording to fetch.
    """
    print(
        f"  waiting: the stream has not started; checking every {interval}s "
        "(Ctrl+C stops waiting; this blocks the rest of the Queue)",
        file=sys.stderr,
        flush=True,
    )
    while _is_waiting_capture(plan):
        time.sleep(interval)
        try:
            fresh = resolve_plan(
                plan.url,
                profile,
                output_root,
                resolver,
                wait_for_stream=True,
            )
        except (InvalidLinkError, UserError) as exc:
            print(
                f"  still waiting (the last check failed: {exc})",
                file=sys.stderr,
                flush=True,
            )
            continue
        plan = fresh
        if _is_waiting_capture(plan):
            print(
                "  still waiting (the stream has not started)",
                file=sys.stderr,
                flush=True,
            )
    if isinstance(plan.kind, CaptureKind):
        print(
            "The stream has started; recording from the beginning.",
            file=sys.stderr,
            flush=True,
        )
    else:
        print(
            "The stream ended before recording could begin; "
            "fetching the platform's archive instead.",
            file=sys.stderr,
            flush=True,
        )
    return plan


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
    wait_interval: int = DEFAULT_WAIT_INTERVAL,
    skip_existing: bool = True,
    confirm: ConfirmCallable | None = None,
    playlist_limit: int = DEFAULT_PLAYLIST_LIMIT,
) -> ItemResult:
    """Run a single Item; return its outcome.

    Plan lines and the per-Item one-line result are printed on stderr.
    The caller aggregates ``ItemResult`` values into a summary.

    A Capture announces how to stop it -- a single interrupt -- before
    recording begins, so the user never has to guess (ticket #10).
    With ``wait_for_stream``, a Capture for a stream that has not
    started yet is waited for: the Item polls until the stream goes
    live and only then records (ticket #11). An interrupt during the
    executor is not an error to recover from but an aftermath to finish
    (``vicxiun.capture.after_interrupt``): a Capture keeps its
    recording, a Download is cleaned of partial files (ticket #10). In
    both cases the outcome is an ``ItemResult`` whose error is
    ``KeyboardInterrupt``, which the Queue sees and stops on. An
    interrupt while *waiting* surfaces through ``run_queue``'s handler
    instead: nothing has been recorded, so there is no aftermath.

    With ``skip_existing`` (ticket #14), an Item whose target file is
    already on disk is skipped without running the executor: the Plan
    (which needs one metadata request) says exactly where the file
    would land, and it is already there. The check sits before the
    stop-instruction and the wait-for-stream loop, so a Capture whose
    file exists is skipped without waiting for a stream. The caller
    records the skip, so later runs skip the resolve too.

    With a ``confirm`` callback (ticket #15), a playlist whose
    ``playlist_count`` is above ``playlist_limit`` asks before it
    starts -- the callback is given the count of Items found and
    answers whether the run may begin. Declining downloads nothing
    for that Item and stops the Queue; a limit of 0 (or no callback)
    never asks.

    Disk space is protected at two floors (ticket #12). Before a
    Capture starts, the target volume must hold the pre-flight floor
    -- below it the Capture does not start and the Item fails; a
    Download is exempt. While any Item is written, free space is
    watched through the progress hook; falling below the running
    floor raises the KeyboardInterrupt a deliberate stop raises, so
    the aftermath -- a Capture's kept recording, a Download cleaned
    of partials -- and the Queue's interrupt follow-through are
    exactly the deliberate-stop behaviour.
    """
    try:
        plan = resolve_plan(link, profile, output_root, resolver, wait_for_stream)
    except (InvalidLinkError, UserError) as exc:
        print(str(exc), file=sys.stderr)
        return ItemResult(link=link, path=None, error=exc)

    if skip_existing and plan.target_path.is_file():
        print(
            f"{link}: skipped (file already present: {plan.target_path})",
            file=sys.stderr,
        )
        return ItemResult(link=link, path=plan.target_path, error=None, skipped=True)

    print(
        f"{link}: {plan.kind.name} with profile {plan.profile.name}",
        file=sys.stderr,
    )
    print(f"  target: {plan.target_path}", file=sys.stderr)

    # A very large playlist asks before it starts (ticket #15): the
    # count comes from the resolve, so the question is asked before any
    # of the playlist is fetched. The statement of how many Items were
    # found belongs here, whatever confirm implementation answers it.
    # Declining is not a failure -- nothing was downloaded and the
    # Queue stops cleanly.
    if (
        confirm is not None
        and playlist_limit > 0
        and plan.playlist_count > playlist_limit
    ):
        print(
            f"This playlist holds {plan.playlist_count} Items. Downloading "
            "all of them may take a long time and fill your disk.",
            file=sys.stderr,
        )
        if not confirm(plan.playlist_count):
            print(
                f"{link}: declined (playlist of {plan.playlist_count} Items; "
                "nothing was downloaded)",
                file=sys.stderr,
            )
            return ItemResult(link=link, path=None, error=None, declined=True)

    if isinstance(plan.kind, CaptureKind):
        print(STOP_INSTRUCTION, file=sys.stderr)
    if plan.wait_for_stream and _is_waiting_capture(plan):
        plan = _wait_for_stream_start(
            plan, profile, output_root, resolver, wait_interval
        )
    # Before a Capture starts, the pre-flight floor applies (ticket
    # #12) -- here, after any wait, so the Plan is the final one: a
    # device that cannot promise the space is not handed a recording at
    # all. A Download is deliberately exempt; the running check covers
    # it alone. The refusal is a per-Item failure and the Queue moves
    # on, as with any failure.
    if isinstance(plan.kind, CaptureKind):
        refusal = capture_pre_flight(plan.target_path)
        if refusal is not None:
            result = ItemResult(link=link, path=None, error=UserError(refusal))
            _record_failure(result)
            return result
    # While anything is written, free space is watched (ticket #12):
    # below the running floor the hook stops the transfer exactly the
    # way a deliberate stop does -- a KeyboardInterrupt -- so the
    # aftermath is the interrupt path's, unchanged. The watch composes
    # with the progress printer, so progress lines still flow.
    watch = SpaceWatch(
        plan.target_path, RUNNING_FLOOR_BYTES, follow=default_progress_printer()
    )
    # Snapshot after any wait, just before the executor runs: on a
    # Download interrupt the cleanup deletes only a file the Item
    # itself created; anything on disk beforehand is a completed record
    # and stays (ticket #13). The wait can replace the Plan (and so the
    # target path) with a fresh one, so the snapshot follows it.
    preexisting = plan.target_path.is_file()
    try:
        actual = executor(plan, watch)
        if watch.tripped:
            # The hook's KeyboardInterrupt was converted to a graceful
            # finish by yt-dlp's live fragment downloader (it catches
            # the interrupt for live streams and finalises instead of
            # propagating): end the Item as a stop just the same.
            raise KeyboardInterrupt("stopped by the low-space check")
    except KeyboardInterrupt:
        detail, kept = after_interrupt(plan, preexisting)
        result = ItemResult(link=link, path=kept, error=KeyboardInterrupt(detail))
        _record_failure(result)
        return result
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
    ``results`` and are named in the summary. ``declined`` is True
    when the run was stopped by the user declining a very large
    playlist (ticket #15) -- nothing failed and nothing of that
    playlist was downloaded; the Items after it are named as not run.
    """

    links: tuple[str, ...]
    results: list[ItemResult]
    interrupted: bool = False
    declined: bool = False

    @property
    def successes(self) -> list[ItemResult]:
        return [
            r
            for r in self.results
            if r.error is None and not r.skipped and not r.declined
        ]

    @property
    def skipped(self) -> list[ItemResult]:
        return [r for r in self.results if r.skipped]

    @property
    def failures(self) -> list[ItemResult]:
        return [r for r in self.results if r.error is not None]

    def summary(self) -> str:
        """Render the closing summary: what succeeded, failed, and why.

        One line per Item, then the counts. A skipped Item gets its own
        line and its own count (ticket #14); the skipped segment is
        omitted from the counts line when nothing was skipped, so the
        "N ok, M failed." shape is unchanged for every other Job. When
        the run was interrupted the remaining Items are named as not
        run. A declined playlist is named in its own line (ticket #15),
        and the Items after it are named as not run.
        """
        lines: list[str] = []
        for r in self.results:
            if r.declined:
                lines.append(
                    f"declined: {r.link} (not downloaded without confirmation)"
                )
            elif r.skipped:
                if r.path is not None:
                    lines.append(f"skipped: {r.link} (file already present: {r.path})")
                else:
                    lines.append(f"skipped: {r.link} (in the skip record)")
            elif r.error is None:
                assert r.path is not None
                lines.append(f"ok: {r.link} ({r.path})")
            else:
                lines.append(f"failed: {r.link} ({label_for(r.error)}: {r.error})")
        if self.interrupted:
            not_started = len(self.links) - len(self.results)
            lines.append(f"Interrupted: {not_started} Item(s) were not run.")
        elif self.declined:
            not_started = len(self.links) - len(self.results)
            lines.append(f"Declined: {not_started} Item(s) were not run.")
        declined_items = [r for r in self.results if r.declined]
        counts = f"{len(self.successes)} ok"
        if self.skipped:
            counts += f", {len(self.skipped)} skipped"
        if declined_items:
            counts += f", {len(declined_items)} declined"
        counts += f", {len(self.failures)} failed."
        lines.append(counts)
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
        return _exit_code_for_class(_deepest_named(failures))


def run_queue(
    links: list[str],
    *,
    profile: Profile,
    output_root: Path,
    resolver: ResolverCallable,
    executor: ExecutorCallable,
    wait_for_stream: bool = False,
    wait_interval: int = DEFAULT_WAIT_INTERVAL,
    skip_existing: bool = True,
    confirm: ConfirmCallable | None = None,
    playlist_limit: int = DEFAULT_PLAYLIST_LIMIT,
) -> QueueOutcome:
    """Handle every Item in ``links`` strictly sequentially.

    Items run one at a time, in the order the user typed them. A
    failed Item is recorded and the Queue continues to the next one.
    With ``wait_for_stream`` a scheduled stream is waited out at
    ``wait_interval`` seconds per check (ticket #11), which blocks the
    Items after it -- documented, with the separate-terminal workaround.
    A ``KeyboardInterrupt`` stops the Queue: the Item in flight is
    recorded as interrupted, the rest are marked not run, and the
    Items that already finished are untouched. The low-space running
    floor (ticket #12) stops a transfer the same way -- the raised
    exception is a ``KeyboardInterrupt``, so the Queue stops and the
    later Items are named as not run, exit 130 and all.

    With ``skip_existing`` (the default, ticket #14), a link named in
    the skip record beside the output is skipped before any resolve --
    so re-running a completed Queue downloads nothing, not even a
    metadata request. Every Item that finishes (or is skipped by file
    presence inside ``run_item``) is added to the record, which is
    rewritten after each one: an interrupt mid-Queue still keeps every
    completed Item in the record. ``skip_existing=False`` runs every
    link again; the record itself is still kept up to date.

    With a ``confirm`` callback (ticket #15), a playlist above
    ``playlist_limit`` Items asks before it starts; declining stops the
    Queue cleanly -- the declined Item downloads nothing, is not
    written to the record (it is not done), and the Items after it do
    not start. A limit of 0, or no callback, never asks.
    """
    record, warning = SkipRecord.load(output_root / SKIP_RECORD_NAME)
    if warning:
        print(warning, file=sys.stderr)

    results: list[ItemResult] = []
    interrupted = False
    declined = False
    for index, link in enumerate(links):
        if skip_existing and link in record:
            print(f"{link}: skipped (in the skip record)", file=sys.stderr)
            result = ItemResult(link=link, path=None, error=None, skipped=True)
        else:
            try:
                result = run_item(
                    link,
                    profile,
                    output_root,
                    resolver,
                    executor,
                    wait_for_stream=wait_for_stream,
                    wait_interval=wait_interval,
                    skip_existing=skip_existing,
                    confirm=confirm,
                    playlist_limit=playlist_limit,
                )
            except KeyboardInterrupt:
                # An interrupt during planning or reporting -- the Item in
                # flight is recorded as interrupted; nothing after it
                # starts. (An interrupt inside the executor is finished by
                # run_item's aftermath and returns a result.)
                interrupted = True
                result = ItemResult(
                    link=link,
                    path=None,
                    error=KeyboardInterrupt(
                        "interrupted before this Item finished",
                    ),
                )
            if result.error is None and not result.declined:
                # Finished, or skipped because the file is already
                # there: either way the user has it. Noted at once, so
                # an interrupt later in the Queue loses nothing.
                record.add(link)
                record.save()
        results.append(result)
        # run_item finished the interrupt's aftermath itself for an
        # interrupt inside the executor, and reports it as a result
        # whose error is KeyboardInterrupt; an interrupt during
        # planning surfaces through the except above. Either way:
        if isinstance(result.error, KeyboardInterrupt):
            # the run is interrupted -- the Items after it do not start.
            interrupted = True
            break
        if result.declined:
            # The user said no to a very large playlist (ticket #15):
            # nothing was downloaded, and the Items after it do not
            # start.
            declined = True
            break
    return QueueOutcome(
        links=tuple(links),
        results=results,
        interrupted=interrupted,
        declined=declined,
    )


__all__ = [
    "DEFAULT_PLAYLIST_LIMIT",
    "DEFAULT_WAIT_INTERVAL",
    "SKIP_RECORD_NAME",
    "ConfirmCallable",
    "ItemResult",
    "QueueOutcome",
    "SkipRecord",
    "label_for",
    "run_item",
    "run_queue",
]
