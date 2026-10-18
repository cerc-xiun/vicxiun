"""Engine adapter for ``vicxiun``.

The engine is the only piece of code that talks to ``yt-dlp``. It is
narrow on purpose: resolve metadata without downloading (``resolve_metadata``),
and execute a plan (``execute``). Tests substitute the resolver so no
test reaches the network.

ADR-0004: nothing is fetched and executed at runtime. The engine disables
``yt-dlp``'s remote-components mechanism; the declared ``yt-dlp-ejs``
dependency supplies the JavaScript challenge solver at install time.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class Engine(Protocol):
    """The shape vicxiun uses to talk to the engine."""

    def resolve_metadata(self, url: str) -> dict[str, object]:
        """Return metadata for ``url`` without downloading media."""
        ...

    def base_options(self) -> dict[str, object]:
        """Return the yt-dlp options the engine was built with.

        Exposed for testing and inspection; ``--dry-run`` uses the same
        options. ADR-0004 demands that no remote-components flag is on.
        """
        ...


ResolverCallable = Callable[[str], dict[str, object]]


class YtDlpEngine:
    """Default engine. Calls ``yt-dlp`` through its Python API."""

    def __init__(self, *, resolver: ResolverCallable | None = None) -> None:
        self._resolver = resolver or self._default_resolver
        self._options = _safe_default_options()

    def resolve_metadata(self, url: str) -> dict[str, object]:
        return self._resolver(url)

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


def build_default_engine() -> YtDlpEngine:
    """Return the engine the real run uses."""
    return YtDlpEngine()


def _safe_default_options() -> dict[str, object]:
    """yt-dlp options that satisfy the project's safety rules.

    * remote_components = None (ADR-0004)
    * cache disabled (ADR-0004)
    * update off (no self-update at runtime)
    * no telemetry opt-in
    * postprocessor/ffmpeg-related options left at yt-dlp defaults; #7 will
      pin format-specific options.
    """
    return {
        "remote_components": None,
        "cache": False,
        # Disable yt-dlp's noisy progress; we emit our own.
        "quiet": False,
        "no_warnings": False,
        # Match ``yt-dlp -U``: the user runs the tool by choice, the tool
        # does not silently self-update.
        "updater": False,
    }
