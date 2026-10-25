"""The ``vicxiun`` command line.

This module is deliberately thin. It owns argument parsing, the process
exit code, and the wiring of the engine seams. The per-Item execution
and the end-of-run summary live in the Queue (``vicxiun.queue``,
ticket #13); ``--dry-run`` continues to print Plans without downloading
(``--dry-run`` shares the real run's Plan code path).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from vicxiun import __version__
from vicxiun.engine import build_default_engine
from vicxiun.errors import UnexpectedFailure, UserError
from vicxiun.plan import (
    InvalidLinkError,
    Plan,
    profile_from_name,
    render_summary,
    resolve_plan,
)
from vicxiun.prereqs import run as run_prereq_check
from vicxiun.queue import (
    DEFAULT_PLAYLIST_LIMIT,
    DEFAULT_WAIT_INTERVAL,
    ConfirmCallable,
    ExecutorCallable,
    ResolverCallable,
    run_queue,
)
from vicxiun.settings import KNOWN_KEYS
from vicxiun.settings import load as load_settings
from vicxiun.settings import save as save_settings

PROG = "vicxiun"

# Module-level swappable seams for the metadata resolver and the executor.
# Tests install fakes here so the CLI exercise stays offline. The
# executor receives a Plan and an optional progress callback.
_resolver: ResolverCallable | None = None
_executor: ExecutorCallable | None = None
_prereq_check: Callable[[], int] | None = None
_confirm: ConfirmCallable | None = None


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


def set_confirm(confirm: ConfirmCallable) -> None:
    """Replace the large-playlist confirmation used by ``main`` (test seam)."""
    global _confirm
    _confirm = confirm


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


def _confirm_playlist(count: int) -> bool:
    """Ask the user to confirm a very large playlist (ticket #15).

    Falls back to the real prompt in production; the Queue has already
    printed what the playlist holds. An empty answer, a closed stdin
    (EOF) or anything but yes declines, so a non-interactive run (for
    example a script piping links in) downloads nothing rather than
    guessing.
    """
    if _confirm is not None:
        return _confirm(count)
    try:
        answer = input("Continue? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the ``vicxiun`` command."""
    from vicxiun.plan import _PROFILES as PROFILES

    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Download finished videos and capture live streams.\n\n"
            "Choices made once are remembered: run 'vicxiun settings' to "
            "show or change saved settings. Any flag overrides a saved "
            "value for that run only."
        ),
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
            "record. A waiting Item blocks the rest of the Queue. With "
            "--dry-run this is reported in the Plan but does not block."
        ),
    )
    parser.add_argument(
        "--wait-interval",
        type=int,
        default=DEFAULT_WAIT_INTERVAL,
        metavar="SECONDS",
        help=(
            "With --wait-for-stream, how often to check whether the "
            "stream has started, in seconds. Defaults to 60."
        ),
    )
    parser.add_argument(
        "--profile",
        default=None,
        choices=tuple(sorted(PROFILES)),
        help=(
            "Output Profile to use. Defaults to your saved setting (see "
            "'vicxiun settings'), else phone-safe, which plays on any "
            "phone without further thought. 'best' reaches for the "
            "highest quality available; 'audio' yields audio only."
        ),
    )
    parser.add_argument(
        "--folder",
        default=None,
        help=(
            "Override the output folder. Defaults to your saved setting "
            "(see 'vicxiun settings'), else the platform default "
            "(~/Videos/vicxiun on PC, /sdcard/Movies/vicxiun on Termux). "
            "Relative paths are interpreted against the current directory."
        ),
    )
    parser.add_argument(
        "--from-file",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "Read links from FILE, one per line, handled after any "
            "links given on the command line. Blank lines and lines "
            "starting with '#' are ignored, so the list can carry notes."
        ),
    )
    parser.add_argument(
        "--no-skip",
        action="store_true",
        help=(
            "Handle every link again even when its file is already in "
            "the output folder or the skip record marks it done. By "
            "default such Items are skipped, so re-running a Queue is "
            "safe and downloads nothing twice."
        ),
    )
    parser.add_argument(
        "--playlist-limit",
        type=int,
        default=DEFAULT_PLAYLIST_LIMIT,
        metavar="N",
        help=(
            "Ask for confirmation before handling a playlist with "
            "more than N Items, so a link that turns out to be a "
            "whole channel is never fetched without your OK. Defaults "
            "to 50; 0 turns the confirmation off."
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


def settings_path() -> Path:
    """The settings file, in the platform's configuration location.

    Tests monkeypatch this so no test reads or writes the developer's
    real settings file.
    """
    from vicxiun.platform import settings_path as _settings_path

    return _settings_path()


def read_links_file(path: Path) -> list[str]:
    """Read a prepared list of links, one per line, in file order.

    Blank lines and lines whose first non-space character is ``#`` are
    ignored (ticket #14), so the list can carry notes. A ``#`` inside a
    line is part of the link -- only a whole-line comment is a comment.
    The BOM is tolerated, because Windows editors add one.
    """
    text = path.read_text(encoding="utf-8-sig")
    links: list[str] = []
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        links.append(entry)
    return links


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command described by ``argv`` and return the exit code.

    Exit codes follow the project contract:

    * 0 — every Item downloaded or captured.
    * 1 — some Items succeeded, some failed (partial success).
    * 2 — user error (bad arguments, bad link, bad path).
    * 3 — a Prerequisite program is missing.
    * 4 — a refused Item (DRM or paywall).
    * 5 — unexpected failure (anything else that bubbles up).
    * 130 — the run was interrupted; completed Items stay finished.

    The Prerequisite check still runs even with a link, so a missing
    ffmpeg is announced before anything else. ``--version`` and
    ``--help`` are handled by argparse before the check fires.
    ``settings`` is dispatched by hand before argparse: the ``links``
    positional takes every argument, so a subparser would swallow
    links that merely look like words.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "settings":
        return _run_settings(argv[1:])

    parser = build_parser()
    args = parser.parse_args(argv)

    # The prepared list, if any, is argument handling: a file that
    # cannot be read is a user error (exit 2) before anything runs.
    # Command-line links come first, then the file's, in file order.
    links = list(args.links)
    if args.from_file is not None:
        from_file = args.from_file.expanduser()
        try:
            links.extend(read_links_file(from_file))
        except OSError as exc:
            print(
                f"error: cannot read --from-file {from_file}: {exc}",
                file=sys.stderr,
            )
            return 2

    # Saved settings are consulted before the run; a flag given on
    # this run wins for this run only and is never written back.
    saved, warning = load_settings(settings_path())
    if warning:
        print(warning, file=sys.stderr)

    code = _run_prereq_check()
    if code != 0:
        return code

    if not links:
        parser.error("at least one LINK is required")

    if args.wait_interval < 1:
        parser.error("--wait-interval must be a whole number of seconds, at least 1")

    if args.playlist_limit < 0:
        parser.error("--playlist-limit must be 0 (off) or a positive number of Items")

    try:
        profile = profile_from_name(
            args.profile or saved.get("profile") or "phone-safe"
        )
    except UserError:
        # A hand-edited settings file can hold an unknown Profile;
        # fall back to the default rather than crash (ticket #16).
        name = saved.get("profile", "")
        print(
            f"Ignoring unknown profile {name!r} in {settings_path()}; "
            "using phone-safe.",
            file=sys.stderr,
        )
        profile = profile_from_name("phone-safe")
    folder = args.folder or saved.get("folder")
    output_root = Path(folder).expanduser() if folder else default_output_root()

    if args.dry_run:
        return _run_dry_run(parser, args, links, profile, output_root)

    return _run_real(parser, args, links, profile, output_root)


# ---------------------------------------------------------------------------
# Dry-run path (preserved from #5/#6; unchanged behaviour for --dry-run)
# ---------------------------------------------------------------------------


def _run_dry_run(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    links: list[str],
    profile,
    output_root: Path,
) -> int:
    """Resolve each Item to a Plan, print it, and stop.

    Behaviour is the same as before ticket #8 -- only the real run
    path is new. ``links`` is the command-line links plus any
    ``--from-file`` entries, already expanded (ticket #14). Skipping
    does not apply here: a preview resolves and prints every link, and
    the no-write guarantee means it never reads or writes the skip
    record.

    Exit codes follow the same contract as a real run (ticket #24):
    a partly planned Job is 1, a wholly refused Job is 2, and a Job
    that planned nothing because something unexpected was raised is 5.
    """
    plans: list[Plan] = []
    failures: list[tuple[str, BaseException]] = []
    for link in links:
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
        except Exception as exc:
            # A surprise must not escape as a traceback: name it the way
            # the Queue names one, and count it for the exit code.
            # ``KeyboardInterrupt`` is a BaseException, so Ctrl+C still
            # interrupts the preview instead of being recorded here.
            error = UnexpectedFailure(str(exc))
            failures.append((link, error))
            print(f"{link}: unexpected ({error})", file=sys.stderr)
            continue
        plans.append(plan)

    for plan in plans:
        print(plan.render(), end="")
    print(render_summary(plans), end="")

    if failures and plans:
        return 1
    if failures:
        if any(isinstance(error, UnexpectedFailure) for _, error in failures):
            return 5
        return 2
    return 0


# ---------------------------------------------------------------------------
# Real run path (tickets #8 and #13; execution lives in vicxiun.queue)
# ---------------------------------------------------------------------------


def _run_real(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    links: list[str],
    profile,
    output_root: Path,
) -> int:
    """Execute every Item for real through the Queue and report results.

    The Queue (``vicxiun.queue``) runs the Items strictly sequentially,
    continues past failures, and stops cleanly on an interrupt
    (ticket #13): completed Items stay finished, the rest are named as
    not run, and the exit code reflects partial success or the
    interrupt. With the default skipping (ticket #14) it also skips
    Items the user already has; ``--no-skip`` turns that off.

    The closing summary is on stderr, alongside the per-Item result
    lines and the progress lines, so a single coherent stream carries
    everything the user sees while the run happens.
    """
    outcome = run_queue(
        links,
        profile=profile,
        output_root=output_root,
        resolver=_resolve_metadata,
        executor=_execute_plan,
        wait_for_stream=args.wait_for_stream,
        wait_interval=args.wait_interval,
        skip_existing=not args.no_skip,
        confirm=_confirm_playlist,
        playlist_limit=args.playlist_limit,
    )
    print(outcome.summary(), end="", file=sys.stderr)
    return outcome.exit_code()


# ---------------------------------------------------------------------------
# Settings command (ticket #16; no prereq check, no link needed)
# ---------------------------------------------------------------------------

_SETTINGS_USAGE = """\
usage: vicxiun settings [KEY [VALUE]]

Show or change saved settings. Without arguments, show every setting,
its current value, and where the settings file lives.

  vicxiun settings                show all settings
  vicxiun settings profile        show one setting
  vicxiun settings profile best   save a value

Keys: {keys}. A flag on a normal run overrides a saved value for that
run only and is never saved.
""".format(keys=", ".join(KNOWN_KEYS))


def _builtin_default(key: str) -> str:
    """The value used when nothing is saved for ``key``."""
    if key == "profile":
        return "phone-safe"
    return str(default_output_root())


def _show(key: str, saved: dict[str, str]) -> None:
    value = saved.get(key)
    if value is None:
        print(f"{key} = {_builtin_default(key)}  (built-in default)")
    else:
        print(f"{key} = {value}  (saved)")


def _run_settings(args: list[str]) -> int:
    """Show or change saved settings (ticket #16).

    ``settings``            show every setting and its source
    ``settings KEY``        show one setting
    ``settings KEY VALUE``  save VALUE for KEY, for every later run
    """
    path = settings_path()
    saved, warning = load_settings(path)
    if warning:
        print(warning, file=sys.stderr)

    if args and args[0] in ("-h", "--help"):
        print(_SETTINGS_USAGE, end="")
        return 0

    if not args:
        for key in KNOWN_KEYS:
            _show(key, saved)
        print(f"Settings file: {path}")
        return 0

    key = args[0]
    if key not in KNOWN_KEYS:
        print(
            f"error: unknown setting {key!r}; known settings: "
            f"{', '.join(KNOWN_KEYS)}. Run 'vicxiun settings --help'.",
            file=sys.stderr,
        )
        return 2

    if len(args) == 1:
        _show(key, saved)
        return 0

    if len(args) > 2:
        print(
            f"error: unexpected arguments {' '.join(args[2:])!r}. "
            "Run 'vicxiun settings --help'.",
            file=sys.stderr,
        )
        return 2

    value = args[1]
    if not value:
        print(f"error: {key} cannot be empty.", file=sys.stderr)
        return 2
    if key == "profile":
        try:
            profile_from_name(value)
        except UserError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    saved[key] = value
    save_settings(path, saved)
    print(f"Saved: {key} = {value}")
    print(f"Settings file: {path}")
    return 0
