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

from vicxiun.errors import (
    PrerequisiteMissing,
    RefusedContent,
    UnexpectedFailure,
    UserError,
)
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
    "requires purchase",
    "subscription required",
)
# yt-dlp phrases that mean a Prerequisite program (almost always ffmpeg
# these days) is missing from the machine.
_PREREQ_PHRASES = (
    "ffmpeg",
    "node",
    "no such file",
    "executable",
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
ExecutorCallable = Callable[
    [Plan, Callable[[dict[str, object]], None] | None], Path
]


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
        return self._resolver(url)

    def execute(
        self,
        plan: Plan,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> Path:
        """Run the plan and return the actual file path produced.

        Any ``yt_dlp.DownloadError`` raised by the executor (real or
        substituted) is translated to one of ``vicxiun``'s named errors
        at this seam. This is the right place for the translation: the
        boundary is the same for the real run and for tests that
        inject a DownloadError to verify the mapping.
        """
        try:
            return self._executor(plan, on_progress)
        except _DownloadError as exc:  # type: ignore[misc]
            _translate_download_error(exc)
            # Unreachable: _translate_download_error always raises.
            raise UnexpectedFailure(str(exc)) from exc

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
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False, process=True)
        # ``extract_info`` returns the processed info dict; we surface it
        # as a plain dict so tests are not coupled to yt-dlp internals.
        return dict(info) if info is not None else {}

    @staticmethod
    def _default_executor(
        plan: Plan,
        on_progress: Callable[[dict[str, object]], None] | None,
    ) -> Path:
        """Run the plan through yt-dlp's Python API and return the file.

        Maps yt-dlp's ``DownloadError`` to the project's named errors so
        the CLI can give a stable exit code for each cause.
        """
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError

        options = _build_executor_options(plan, on_progress)

        try:
            with YoutubeDL(options) as ydl:
                # For a finished video we download; for a live stream we
                # record with ``--live-from-start``. ``download=True`` is
                # the default, but we set it explicitly so the call site
                # is self-documenting.
                ydl.download([plan.url])
        except DownloadError as exc:
            _translate_download_error(exc)
            # Unreachable: _translate_download_error always raises.
            raise UnexpectedFailure(str(exc)) from exc

        return plan.target_path


def build_default_engine() -> YtDlpEngine:
    """Return the engine the real run uses."""
    return YtDlpEngine()


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
        # Disable yt-dlp's noisy progress; we emit our own.
        "quiet": False,
        "no_warnings": False,
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
    output template and progress hook. The output template is the
    absolute target path so yt-dlp writes the file in the right place on
    -- the first try.
    """
    options = _safe_default_options()
    options["quiet"] = True
    options["no_warnings"] = True
    options["format"] = plan.profile.format_selector
    options["outtmpl"] = str(plan.target_path)
    # Captures record to MKV with live-from-start semantics.
    if isinstance(plan.kind, CaptureKind):
        options["hls_use_mpegts"] = True
        options["live_from_start"] = True
        options["wait_for_stream"] = bool(plan.wait_for_stream)
    if on_progress is not None:
        options["progress_hooks"] = [on_progress]
    return options


def _translate_download_error(exc: BaseException) -> None:
    """Map a yt-dlp ``DownloadError`` to a named ``vicxiun`` error.

    The mapping is best-effort: phrases in the exception message are
    matched against DRM and Prerequisite keywords. Anything we cannot
    classify becomes ``UserError`` (the most common case: the link is
    dead, the video was removed, or the user typed something the engine
    did not understand).
    """
    message = str(exc).lower()

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

    # Anything else is a user-fixable error: the link is bad, the video
    # was removed, etc. We surface the message rather than wrapping it
    # in something opaque.
    raise UserError(f"could not download {exc}") from exc


# Lazy-resolved type alias for the engine's download-error type. We do
# not import ``yt_dlp`` at module load time (the test suite runs without
# yt-dlp in some envs) so the class is fetched on first use.
_DownloadError: type[BaseException]
try:
    from yt_dlp.utils import DownloadError as _DownloadError
except ImportError:  # pragma: no cover - only triggers when yt-dlp missing
    class _DownloadError(BaseException):  # type: ignore[no-redef]
        """Placeholder used only when yt-dlp is not importable."""


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


def default_progress_printer(stream: TextIO | None = None) -> Callable[
    [dict[str, object]], None
]:
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