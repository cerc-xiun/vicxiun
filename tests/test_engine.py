"""Tests for the engine adapter.

The engine is the only piece of code that touches the network. Every test
in this file substitutes the engine with a fake and asserts behaviour at
the seam. No test reaches the network, and the engine default is asserted
to disable remote-component fetching.
"""

from __future__ import annotations

from vicxiun.engine import Engine, YtDlpEngine, build_default_engine


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
    assert opts.get("remote_components") in (None, False)
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
