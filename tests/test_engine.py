"""Tests for the engine adapter.

The engine is the only piece of code that touches the network. Every test
in this file substitutes the engine with a fake and asserts behaviour at
the seam. No test reaches the network, and the engine default is asserted
to disable remote-component fetching.
"""

from __future__ import annotations

from vicxiun.engine import (
    Engine,
    YtDlpEngine,
    build_default_engine,
    normalise_resolver_result,
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
