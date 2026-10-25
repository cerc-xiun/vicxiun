"""Engine adapter for ``vicxiun``.

The engine is the only piece of code that talks to ``yt-dlp``. It is
narrow on purpose: resolve metadata without downloading (``resolve_metadata``),
and execute a plan (``execute``). Tests substitute the resolver and
executor so no test reaches the network.

ADR-0004: nothing is fetched and executed at runtime. The engine disables
``yt-dlp``'s remote-components mechanism; the declared ``yt-dlp-ejs``
dependency supplies the JavaScript challenge solver at install time.

The engine also disables yt-dlp's own update check and any telemetry,
per the ticket #8 acceptance criteria.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TextIO

from yt_dlp.utils import DownloadError

from vicxiun.errors import (
    PrerequisiteMissing,
    RefusedContent,
    TransientError,
    UserError,
)
from vicxiun.paths import entry_target
from vicxiun.plan import CaptureKind, Plan

# Recognise DRM / paywall phrases yt-dlp surfaces in its error messages.
# The list is conservative: matches in yt-dlp's error string that look like
# "this video is DRM-protected" or "sign in to confirm you are not a bot"
# (when a cookie is required and absent). This is the fallback layer; the
# primary check is the engine's ``DownloadError`` reason classification.
_DRM_PHRASES = (
    "drm",
    "protected by a sign in",
    "sign in to confirm",
    "premium",
    "members only",
)
# yt-dlp phrases that mean a Prerequisite program (almost always ffmpeg
# these days) is missing from the machine. Kept narrow on purpose: a
# vague phrase like "no such file" will match almost any error and
# cross-bleed into unrelated failure modes.
_PREREQ_PHRASES = (
    "ffmpeg",
    "node",
)
# Phrases that mean "the network failed, the link may be fine". A
# deliberately short positive list: yt-dlp words an HTTPError and a
# TransportError the same way ("Unable to download webpage: ..."), so a
# negative rule ("anything but a 4xx") would guess. A 4xx matches none
# of these and stays a user error (ticket #33).
_TRANSIENT_PHRASES = (
    "timed out",
    "timeout",
    "getaddrinfo failed",
    "temporary failure in name resolution",
    "connection reset",
    "connection aborted",
    "connection refused",
    "network is unreachable",
    "remote end closed connection",
    "incomplete read",
    "http error 429",
    "http error 5",
)


class Engine(Protocol):
    """The shape vicxiun uses to talk to the engine."""

    def resolve_metadata(self, url: str) -> dict[str, object]:
        """Return metadata for ``url`` without downloading media."""
        ...

    def execute(
        self,
        plan: Plan,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> Path:
        """Run the plan and return the actual file path produced.

        ``on_progress`` is invoked with each yt-dlp progress dict while the
        download runs. Tests pass a recorder; the CLI passes a printer.
        """
        ...

    def base_options(self) -> dict[str, object]:
        """Return the yt-dlp options the engine was built with.

        Exposed for testing and inspection; ``--dry-run`` uses the same
        options. ADR-0004 demands that no remote-components flag is on,
        and ticket #8 demands that update and telemetry are off.
        """
        ...


ResolverCallable = Callable[[str], dict[str, object]]
ExecutorCallable = Callable[[Plan, Callable[[dict[str, object]], None] | None], Path]


def normalise_resolver_result(info: object) -> dict[str, object]:
    """Map yt-dlp's raw info dict onto vicxiun's metadata keys.

    yt-dlp names the video's identifier ``id``; ``plan._to_metadata``
    requires ``video_id``. Its raw dict also has no top-level
    human-readable format description, so one is built from the selected
    stream's ``resolution`` and ``ext`` -- the Plan prints it as the
    ``Format`` line (display only). A link with no formats at all (a
    scheduled stream that has not started -- ticket #28) has no selected
    stream either, so both fall back: the description reads ``unknown``
    and ``ext`` is empty. Anything the raw dict already supplies is kept,
    so the resolver result still surfaces everything yt-dlp reported.
    """
    if not isinstance(info, dict):
        return {}
    result = dict(info)
    if not result.get("video_id"):
        result["video_id"] = result.get("id") or ""
    # ``ext`` is a per-format field in yt-dlp: with no formats selected
    # the key is absent, but ``plan._to_metadata`` requires it (ticket #28).
    if not result.get("ext"):
        result["ext"] = ""
    if not result.get("format_description"):
        resolution = str(result.get("resolution") or "unknown")
        ext = str(result.get("ext") or "")
        suffix = f" {ext}" if ext else ""
        result["format_description"] = f"{resolution}{suffix}"
    return result


class YtDlpEngine:
    """Default engine. Calls ``yt-dlp`` through its Python API."""

    def __init__(
        self,
        *,
        resolver: ResolverCallable | None = None,
        executor: ExecutorCallable | None = None,
    ) -> None:
        self._resolver = resolver or self._default_resolver
        self._executor = executor or self._default_executor
        self._options = _safe_default_options()

    def resolve_metadata(self, url: str) -> dict[str, object]:
        """Return metadata for ``url`` without downloading media.

        Any ``yt_dlp.DownloadError`` raised by the resolver (real or
        substituted) is translated to one of ``vicxiun``'s named errors
        here, exactly as ``execute`` does on the download side. A
        resolve-time yt-dlp failure must never escape as a raw
        ``DownloadError`` traceback (ticket #28).
        """
        try:
            return self._resolver(url)
        except DownloadError as exc:
            _translate_download_error(exc)

    def execute(
        self,
        plan: Plan,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> Path:
        """Run the plan and return the actual file path produced.

        Any ``yt_dlp.DownloadError`` raised by the executor (real or
        substituted) is translated to one of ``vicxiun``'s named errors
        at this seam. The boundary is the same for the real run and for
        tests that inject a DownloadError to verify the mapping.
        """
        try:
            return self._executor(plan, on_progress)
        except DownloadError as exc:
            _translate_download_error(exc)

    def base_options(self) -> dict[str, object]:
        return dict(self._options)

    @staticmethod
    def _default_resolver(url: str) -> dict[str, object]:
        """Call yt-dlp's Python API with ``download=False``.

        The cache is disabled (ADR-0004): no files are written.
        """
        # Imported lazily so unit tests that substitute the resolver do
        # not pay the import cost and so the test suite can run without
        # yt-dlp installed for engine tests that never hit this branch.
        from yt_dlp import YoutubeDL

        options = _safe_default_options()
        options["quiet"] = True
        options["no_warnings"] = True
        # Resolver-only. yt-dlp treats "no formats" as an *expected* error
        # for some links -- most importantly a scheduled stream
        # (``live_status="is_upcoming"``: "this live event will begin
        # in ..."), whose info dict is thrown away before planning runs
        # unless this is set (ticket #28). Deliberately NOT part of
        # ``_safe_default_options``: the executor shares those, and a
        # link with no formats must still fail at execute time.
        options["ignore_no_formats_error"] = True
        # Inside a playlist, resolve the entries flat: the metadata
        # request must report how many Items the playlist holds (ticket
        # #15) without fetching every entry's metadata first -- a
        # five-thousand-video channel would otherwise resolve for
        # hours. A direct video link is unaffected.
        options["extract_flat"] = "in_playlist"
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False, process=True)
        # ``extract_info`` returns the processed info dict; we surface it
        # as a plain dict so tests are not coupled to yt-dlp internals.
        # yt-dlp's key names differ from vicxiun's metadata contract
        # (``id`` vs ``video_id``, no ``format_description``), so they
        # are normalised here rather than trusted downstream.
        return normalise_resolver_result(info)

    @staticmethod
    def _default_executor(
        plan: Plan,
        on_progress: Callable[[dict[str, object]], None] | None,
    ) -> Path:
        """Run the plan through yt-dlp's Python API and return the file.

        The actual ``yt_dlp.DownloadError`` translation happens in
        ``execute()``, so the inner call here is plain: any error from
        yt-dlp bubbles to the seam where it is mapped to a named error.
        """
        from yt_dlp import YoutubeDL

        if plan.playlist_count > 0:
            return _download_playlist(plan, on_progress)

        options = _build_executor_options(plan, on_progress)

        with YoutubeDL(options) as ydl:
            # ``download=True`` is the default; we set it explicitly so
            # the call site is self-documenting.
            ydl.download([plan.url])

        return plan.target_path


def build_default_engine() -> YtDlpEngine:
    """Return the engine the real run uses."""
    return YtDlpEngine()


def _download_playlist(
    plan: Plan,
    on_progress: Callable[[dict[str, object]], None] | None,
) -> Path:
    """Download a Playlist one entry at a time (ticket #23).

    The Plan's ``target_path`` is the Playlist's output folder. yt-dlp's
    own output templates are deliberately not used to name the entries:
    a template lets yt-dlp choose the filename, which bypasses
    vicxiun's hostile-title sanitisation (spec rule 43). Instead the
    Playlist is resolved flat (the same cheap count-only extraction the
    resolver uses, ticket #15) and each entry is downloaded on its own
    with a fully literal ``outtmpl`` -- the absolute path ``entry_target``
    built through the same sanitiser, traversal neutralisation and
    containment assertion a single video's Plan gets.

    One yt-dlp call per entry means an entry whose final file already
    exists is reported as already downloaded and skipped by yt-dlp's
    own no-overwrite default, so a re-run after a mid-Playlist failure
    fetches only what is missing. The function returns the folder; each
    finished entry is a complete file inside it.

    A ``DownloadError`` from any entry propagates: ``execute()`` maps
    it to a named error and the Queue treats the Item as failed, while
    the entries that finished stay on disk.
    """
    from yt_dlp import YoutubeDL

    folder = plan.target_path
    resolve_options = _safe_default_options()
    resolve_options["quiet"] = True
    resolve_options["no_warnings"] = True
    # Flat extraction: the entries' own metadata is fetched one video at
    # a time below; here only the Playlist's shape is needed.
    resolve_options["extract_flat"] = "in_playlist"
    with YoutubeDL(resolve_options) as ydl:
        info = ydl.extract_info(plan.url, download=False, process=True) or {}
    entries = info.get("entries") or []

    options = _build_executor_options(plan, on_progress)
    for position, entry in enumerate(entries, start=1):
        if entry is None:
            # yt-dlp marks an entry it could not even resolve as None
            # (a deleted video, say). Skip it, as a plain yt-dlp
            # Playlist run would.
            print(
                f"  skipping entry {position}: the platform reports it unavailable",
                file=sys.stderr,
            )
            continue
        raw = entry if isinstance(entry, dict) else {}
        entry_url = str(raw.get("url") or "")
        if not entry_url:
            raise UserError(f"playlist entry {position} has no link to download")
        target = entry_target(
            folder,
            title=str(raw.get("title") or ""),
            video_id=str(raw.get("id") or ""),
            ext=plan.profile.container,
        )
        entry_options = {**options, "outtmpl": str(target)}
        with YoutubeDL(entry_options) as ydl:
            ydl.download([entry_url])
    return folder


def _safe_default_options() -> dict[str, object]:
    """yt-dlp options that satisfy the project's safety rules.

    * ``remote_components`` is an empty set (ADR-0004 -- never fetch
      components from the network).
    * ``cache`` is disabled (ADR-0004 -- no cache files are written).
    * ``updater`` is False (no self-update at runtime).
    * ``enable_telemetry`` is False (no telemetry opt-in).
    * Postprocessor / ffmpeg-related options are left at yt-dlp's
      defaults; the format-specific options are layered in by
      ``_build_executor_options`` on top of these.
    """
    return {
        "remote_components": set(),
        "cache": False,
        # Match ``yt-dlp -U``: the user runs the tool by choice, the tool
        # does not silently self-update.
        "updater": False,
        # Disable yt-dlp's opt-in telemetry channel.
        "enable_telemetry": False,
    }


def _build_executor_options(
    plan: Plan,
    on_progress: Callable[[dict[str, object]], None] | None,
) -> dict[str, object]:
    """Compose the yt-dlp options the executor uses for one plan.

    Starts from the safety defaults and adds the per-Item format,
    format sort, merge container, output template and progress hook. The
    output template is the absolute target path so yt-dlp writes the file
    in the right place on the first try.

    The Profile's ``format_sort`` is layered in here rather than folded
    into the selector, so the choice is expressed as a preference rather
    than a requirement: an unusual video with no H.264 stream still
    yields a playable file (ticket #7, ADR-0002).

    A Capture Plan additionally sets ``live_from_start`` (record from
    the earliest point the platform still holds -- the main acceptance
    criterion of #9) and ``hls_use_mpegts`` (each HLS segment is wrapped
    in MPEG-TS so the MKV container can keep appending fragments and a
    clean stop, deliberate or accidental, leaves a playable file per
    ADR-0007). Waiting for a stream that has not started yet is the
    Queue's job (ticket #11): by the time a Capture Plan reaches the
    engine the stream is live, and ``live_from_start`` records it from
    the beginning.
    """
    options = _safe_default_options()
    options["quiet"] = True
    # yt-dlp picks its progress printer from ``noprogress``, not from
    # ``quiet``, so ``quiet`` alone still draws a bar. Hooks fire either
    # way (the progress printer and the disk-space guard both ride them):
    # this only stops yt-dlp writing a second, duplicate progress line.
    options["noprogress"] = True
    options["no_warnings"] = True
    options["format"] = plan.profile.format_selector
    # The Profile's sort is what expresses its codec and resolution
    # preferences. Passing it alongside the selector is what lets an
    # unusual video degrade to a playable file instead of failing: the
    # limit-form fields demote the unwanted formats without excluding
    # them, so nothing is ever un-selectable (ADR-0002, ticket #7).
    options["format_sort"] = list(plan.profile.format_sort)
    if plan.profile.merge_output_format:
        # Ask ffmpeg to produce this container when it has to merge audio
        # and video. Left unset otherwise, so yt-dlp picks the compatible
        # extension itself rather than re-encoding to satisfy us.
        options["merge_output_format"] = plan.profile.merge_output_format
    options["outtmpl"] = str(plan.target_path)
    if isinstance(plan.kind, CaptureKind):
        options["live_from_start"] = True
        options["hls_use_mpegts"] = True
    if on_progress is not None:
        options["progress_hooks"] = [on_progress]
    return options


def _translate_download_error(exc: DownloadError) -> None:
    """Map a yt-dlp ``DownloadError`` to a named ``vicxiun`` error.

    The mapping is best-effort: phrases in the exception message are
    matched against DRM and Prerequisite keywords. Anything we cannot
    classify becomes ``UserError`` (the most common case: the link is
    dead, the video was removed, or the user typed something the engine
    did not understand). The function always raises.
    """
    # Hyphens are normalised so "members-only" matches the "members only"
    # phrase above; the observed yt-dlp message is hyphenated (ticket #38).
    message = str(exc).lower().replace("-", " ")

    for phrase in _DRM_PHRASES:
        if phrase in message:
            raise RefusedContent(
                "vicxiun refuses to download DRM-protected or "
                "paywalled content (ADR-0003): " + str(exc),
            ) from exc

    for phrase in _PREREQ_PHRASES:
        if phrase in message:
            raise PrerequisiteMissing(
                f"a Prerequisite program is missing: {exc}; "
                "run vicxiun with no arguments to see install instructions",
            ) from exc

    # A network failure is worth another go; a dead link is not. The
    # advisory sentence is part of the message so the per-Item line and
    # the summary both carry it with no second mechanism. There is no
    # retry loop: this only tells the user what to do (ticket #33).
    if any(phrase in message for phrase in _TRANSIENT_PHRASES):
        raise TransientError(
            f"network problem: {exc} — this is usually temporary; "
            "running the same command again may work."
        ) from exc

    # Anything else is a user-fixable error: the link is bad, the video
    # was removed, etc. We surface the message rather than wrapping it
    # in something opaque.
    raise UserError(f"could not download {exc}") from exc


# ---------------------------------------------------------------------------
# Re-export of the metadata shape so the engine module is the single entry
# point for tests that want a ready-made resolver/executor pair.
# ---------------------------------------------------------------------------

__all__ = [
    "Engine",
    "ExecutorCallable",
    "ResolverCallable",
    "YtDlpEngine",
    "build_default_engine",
]


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------


def default_progress_printer(
    stream: TextIO | None = None,
) -> Callable[[dict[str, object]], None]:
    """Return a progress hook that prints one line per yt-dlp event.

    The hook writes a short, single-line update for each progress event.
    The line is flushed immediately so the user gets feedback on a slow
    terminal. ``stream`` defaults to ``sys.stderr``; tests pass a buffer.
    """
    out = stream if stream is not None else sys.stderr

    def hook(d: dict[str, object]) -> None:
        status = str(d.get("status", "?"))
        if status == "downloading":
            percent = d.get("_percent_str") or "?"
            speed = d.get("_speed_str") or "?"
            eta = d.get("_eta_str") or "?"
            line = f"  downloading: {percent} at {speed} ETA {eta}"
        elif status == "finished":
            line = "  finished downloading a fragment"
        elif status == "error":
            line = "  fragment error"
        else:
            line = f"  status: {status}"
        print(line, file=out, flush=True)

    return hook
