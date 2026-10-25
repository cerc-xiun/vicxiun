"""Tests for the engine adapter.

The engine is the only piece of code that touches the network. Every test
in this file substitutes the engine with a fake and asserts behaviour at
the seam. No test reaches the network, and the engine default is asserted
to disable remote-component fetching.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from vicxiun.engine import (
    Engine,
    YtDlpEngine,
    _build_executor_options,
    build_default_engine,
    normalise_resolver_result,
)
from vicxiun.errors import UserError
from vicxiun.plan import (
    CaptureKind,
    DownloadKind,
    Plan,
    profile_from_name,
    resolve_plan,
)


def test_engine_resolver_protocol_is_satisfied_by_callable() -> None:
    def my_resolver(url: str) -> dict[str, object]:
        return {"id": "x", "title": "t", "uploader": "u", "ext": "mp4"}

    engine: Engine = YtDlpEngine(resolver=my_resolver)
    assert engine.resolve_metadata("https://example.com/v") == {
        "id": "x",
        "title": "t",
        "uploader": "u",
        "ext": "mp4",
    }


def test_engine_passes_url_through_unchanged() -> None:
    seen: list[str] = []

    def my_resolver(url: str) -> dict[str, object]:
        seen.append(url)
        return {"id": "x"}

    engine = YtDlpEngine(resolver=my_resolver)
    engine.resolve_metadata("https://example.com/watch?v=abc")
    assert seen == ["https://example.com/watch?v=abc"]


def test_default_engine_disables_remote_component_fetching() -> None:
    """ADR-0004: the engine never enables remote components; the
    declared ``yt-dlp-ejs`` dependency is used instead. We assert the
    option carries the right flag at engine construction time."""
    engine = build_default_engine()
    opts = engine.base_options()
    # ``remote_components`` must be falsy: an empty set, None or False
    # all mean "off". An empty set is what yt-dlp itself treats as off.
    assert not opts.get("remote_components")
    # yt-dlp 2025.04+ removed the ``enable_create_executable`` block;
    # we accept either the legacy or the current default. Update checks
    # are explicitly off.
    assert opts.get("update") in (None, False)


def test_default_engine_disables_update_check() -> None:
    engine = build_default_engine()
    opts = engine.base_options()
    # yt-dlp reads this from "updater", and from CLI the flag is -U.
    # We accept either spelling off.
    updater = opts.get("updater", None)
    if updater is not None:
        # ``updater`` accepts True/False/'only' etc.; any value other than
        # ``False`` is treated as enabled.
        assert updater in (False, None)


def test_default_engine_disables_telemetry() -> None:
    engine = build_default_engine()
    opts = engine.base_options()
    # The CLI-emitted curl-cffi verbose http traffic is also disallowed.
    # We assert no telemetry-opt-in flag is set positive.
    for key in ("enable_telemetry", "telemetry"):
        assert opts.get(key) in (None, False, "off")


def test_resolver_result_maps_yt_dlp_keys_to_the_metadata_contract() -> None:
    """A real yt-dlp info dict carries ``id``, not ``video_id``, and no
    ``format_description``. ``normalise_resolver_result`` maps both onto
    the metadata contract ``plan._to_metadata`` enforces, so a real link
    resolves instead of failing with "engine result missing keys"."""
    raw = {
        "id": "aqz-KE-bpKQ",
        "title": "t",
        "uploader": "u",
        "ext": "mp4",
        "is_live": False,
        "resolution": "1920x1080",
    }
    result = normalise_resolver_result(raw)
    assert result["video_id"] == "aqz-KE-bpKQ"
    assert result["format_description"] == "1920x1080 mp4"
    # Everything yt-dlp reported is still surfaced through.
    assert result["id"] == "aqz-KE-bpKQ"


def test_resolver_result_keeps_explicit_video_id_and_format_description() -> None:
    raw = {
        "id": "x",
        "video_id": "explicit",
        "title": "t",
        "format_description": "already described",
    }
    result = normalise_resolver_result(raw)
    assert result["video_id"] == "explicit"
    assert result["format_description"] == "already described"


def test_resolver_result_of_none_is_an_empty_dict() -> None:
    assert normalise_resolver_result(None) == {}


# ---------------------------------------------------------------------------
# Upcoming streams -- ticket #28
# ---------------------------------------------------------------------------


def test_engine_resolve_metadata_translates_a_yt_dlp_error() -> None:
    """A resolve-time ``DownloadError`` becomes a named vicxiun error.

    The real failure (ticket #28): an upcoming stream made yt-dlp raise
    "This live event will begin in 27 hours" straight through
    ``resolve_metadata``, which escaped as a raw traceback.
    """

    def raising(url: str) -> dict[str, object]:
        raise DownloadError(
            "[youtube] NcnmGiJSpbA: This live event will begin in 27 hours."
        )

    engine = YtDlpEngine(resolver=raising)
    with pytest.raises(UserError) as excinfo:
        engine.resolve_metadata("https://www.youtube.com/live/NcnmGiJSpbA")
    assert "will begin in 27 hours" in str(excinfo.value)


def test_resolver_ignores_no_formats_error_while_the_executor_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ignore_no_formats_error`` is set for the resolver only (ticket #28).

    A scheduled stream has no formats, and yt-dlp treats that as an
    expected error -- so the resolver must ignore it. The executor builds
    its options from the shared ``_safe_default_options``, which must NOT
    carry the flag: a link with no formats still fails at execute time.
    """
    captured: dict[str, object] = {}

    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            captured.update(options)

        def __enter__(self) -> FakeYoutubeDL:
            return self

        def __exit__(self, *exc_info: object) -> bool:
            return False

        def extract_info(
            self, url: str, download: bool, process: bool
        ) -> dict[str, object]:
            return {"id": "x"}

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYoutubeDL)
    engine = YtDlpEngine()
    engine.resolve_metadata("https://example.com/v")

    assert captured["ignore_no_formats_error"] is True
    plan = Plan(
        url="https://example.com/v",
        kind=DownloadKind(),
        profile=profile_from_name("phone-safe"),
        format_description="MP4",
        target_path=Path("/tmp/out/a.mp4"),
    )
    assert "ignore_no_formats_error" not in _build_executor_options(plan, None)


def test_no_formats_upcoming_result_survives_normalisation_and_plans_a_wait(
    tmp_path: Path,
) -> None:
    """A scheduled stream's raw info dict reaches the upcoming branch.

    yt-dlp reports an upcoming event with no formats, so no ``ext`` and no
    ``resolution``: exactly what normalisation used to drop, which made
    ``plan.resolve_plan``'s ``is_upcoming`` branch unreachable against a
    real event (ticket #28). This walks the real hand-off -- raw dict,
    normalisation, plan -- without re-testing the Queue's wait loop.
    """
    raw: dict[str, object] = {
        "id": "NcnmGiJSpbA",
        "title": "Tonight's Stream",
        "uploader": "Some Channel",
        "is_live": False,
        "live_status": "is_upcoming",
        "formats": [],
    }

    plan = resolve_plan(
        "https://www.youtube.com/live/NcnmGiJSpbA",
        profile_from_name("capture"),
        tmp_path,
        lambda url: normalise_resolver_result(raw),
        wait_for_stream=True,
    )

    assert isinstance(plan.kind, CaptureKind)
    assert plan.wait_for_stream is True
    assert plan.format_description == "unknown"
    assert plan.target_path.suffix == ".mkv"
