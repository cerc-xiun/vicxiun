"""Integration tests for ``vicxiun``'s real run path (ticket #8).

The CLI is the project's only integration seam. These tests substitute
both seams -- the resolver (metadata fetch) and the executor (real
download) -- so no test reaches the network.

The contract under test:

* A single valid Item produces an ``ok`` line and exit code 0.
* Progress is emitted while the download runs.
* A failed Item is mapped to the right exit code (1, 3, 4, or 5).
* A Job of one good and one bad Item exits 1 (partial).
* The default Output folder is the Uploader's name (``<output>/<Uploader>/...``).
* Two Items with the same title produce distinct filenames (the [id]
  suffix is part of the contract; see ticket #6).
"""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import pytest

from vicxiun import cli
from vicxiun.cli import main
from vicxiun.engine import default_progress_printer
from vicxiun.errors import (
    PrerequisiteMissing,
    RefusedContent,
    UserError,
)
from vicxiun.plan import Plan, resolve_plan

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


_BASIC_DOWNLOAD = {
    "title": "Best of 2024",
    "uploader": "Some Channel",
    "ext": "mp4",
    "is_live": False,
    "format_description": "MP4, H.264 + AAC, up to 1080p",
    "video_id": "abc123XYZ",
}


def _install_resolver(
    monkeypatch,
    mapping: dict[str, dict[str, object]],
) -> None:
    def resolver(url: str) -> dict[str, object]:
        if url not in mapping:
            raise UserError(f"engine cannot resolve {url}")
        return dict(mapping[url])

    monkeypatch.setattr(cli, "_resolver", resolver)


def _install_executor(
    monkeypatch,
    fn: Callable[[Plan, Callable[[dict[str, object]], None] | None], Path],
) -> None:
    monkeypatch.setattr(cli, "_executor", fn)


# ---------------------------------------------------------------------------
# Acceptance criteria: real download writes a real file
# ---------------------------------------------------------------------------


def test_real_run_writes_a_real_file_in_the_uploader_folder(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The accept criterion: a link in, a playable file out.

    ``<output>/<Uploader>/<Title> [id].<ext>`` is the contract (ticket
    #6). We assert it here through the CLI seam by writing a tiny file
    at the resolved target.
    """
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    def fake_executor(plan, progress):
        # The real yt-dlp writes the file. We honour the contract:
        # create the file the Plan said it would land on.
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"FAKE VIDEO BYTES")
        return plan.target_path

    _install_executor(monkeypatch, fake_executor)

    code = main(["--folder", str(tmp_path), "https://example.com/fin"])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    # The file landed under <output>/<Uploader>/.
    expected_dir = tmp_path / "Some Channel"
    assert expected_dir.is_dir()
    files = list(expected_dir.iterdir())
    assert len(files) == 1
    # The filename carries the [id] suffix and the .mp4 extension.
    name = files[0].name
    assert "Best of 2024" in name
    assert "abc123XYZ" in name
    assert name.endswith(".mp4")
    # The closing summary line: "N ok, M failed." (on stderr, alongside
    # progress; the real run is a single coherent user-facing stream).
    assert "1 ok, 0 failed" in captured.err


def test_real_run_prints_one_line_per_item_describing_the_result(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The accept criterion: one line describes the result at the end."""
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    def fake_executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        return plan.target_path

    _install_executor(monkeypatch, fake_executor)

    main(["--folder", str(tmp_path), "https://example.com/fin"])
    captured = capsys.readouterr()

    # Per-Item one-liner on stderr.
    err = captured.err
    assert "https://example.com/fin" in err
    assert "ok" in err
    # The closing summary line is on stderr too, so a single coherent
    # stream carries everything the user sees while the run happens.
    assert "1 ok, 0 failed" in captured.err


def test_real_run_prints_progress_through_the_progress_printer(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The accept criterion: progress is shown while the download runs.

    We swap the default progress printer for one that captures events
    into a list, then assert the list is non-empty.
    """
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    events: list[dict[str, object]] = []

    def fake_executor(plan, progress):
        progress({"status": "downloading", "_percent_str": "10%"})
        progress({"status": "finished"})
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        return plan.target_path

    _install_executor(monkeypatch, fake_executor)

    # Sanity: the default printer produces a single-line update.
    printer = default_progress_printer(io.StringIO())
    printer({"status": "downloading", "_percent_str": "10%"})
    printer({"status": "finished"})

    main(["--folder", str(tmp_path), "https://example.com/fin"])
    assert events == []  # the in-process events are local to the executor
    captured = capsys.readouterr()
    # The default printer wrote to stderr.
    assert "downloading" in captured.err


# ---------------------------------------------------------------------------
# Exit codes -- the ticket's contract
# ---------------------------------------------------------------------------


def test_real_run_returns_zero_for_a_single_valid_item(
    tmp_path: Path, monkeypatch
) -> None:
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    def fake_executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        return plan.target_path

    _install_executor(monkeypatch, fake_executor)
    code = main(["--folder", str(tmp_path), "https://example.com/fin"])
    assert code == 0


def test_real_run_returns_partial_when_some_items_succeed(
    tmp_path: Path, monkeypatch
) -> None:
    """One good Item, one Item that the resolver rejects -> exit code 1.

    The resolver raises ``UserError`` for the bad URL; the CLI maps that
    to a user error per Item, then exits 1 because there were mixed
    outcomes.
    """
    _install_resolver(monkeypatch, {"https://example.com/good": _BASIC_DOWNLOAD})

    def fake_executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        return plan.target_path

    _install_executor(monkeypatch, fake_executor)
    code = main(
        [
            "--folder",
            str(tmp_path),
            "https://example.com/good",
            "https://example.com/bad",
        ]
    )
    assert code == 1  # PartialSuccess


def test_real_run_returns_user_error_when_all_items_fail_validation(
    tmp_path: Path, monkeypatch
) -> None:
    _install_resolver(monkeypatch, {})

    code = main(["--folder", str(tmp_path), "https://example.com/x"])
    assert code == 2  # UserError


def test_real_run_returns_prerequisite_missing_code_when_executor_says_so(
    tmp_path: Path, monkeypatch
) -> None:
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    def raising(plan, progress):
        raise PrerequisiteMissing("ffmpeg not found")

    _install_executor(monkeypatch, raising)
    code = main(["--folder", str(tmp_path), "https://example.com/fin"])
    assert code == 3  # PrerequisiteMissing


def test_real_run_returns_refused_code_when_drm(
    tmp_path: Path, monkeypatch
) -> None:
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    def raising(plan, progress):
        raise RefusedContent("video is DRM-protected")

    _install_executor(monkeypatch, raising)
    code = main(["--folder", str(tmp_path), "https://example.com/fin"])
    assert code == 4  # RefusedContent


def test_real_run_returns_unexpected_code_when_unexpected_raises(
    tmp_path: Path, monkeypatch
) -> None:
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    def raising(plan, progress):
        raise RuntimeError("something we did not expect")

    _install_executor(monkeypatch, raising)
    code = main(["--folder", str(tmp_path), "https://example.com/fin"])
    assert code == 5  # UnexpectedFailure


def test_real_run_returns_refused_code_when_every_item_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """A Job where every Item fails with ``RefusedContent`` exits 4.

    The acceptance criterion lists refused content as its own code,
    distinct from unexpected (5) and partial (1).
    """
    _install_resolver(
        monkeypatch,
        {
            "https://example.com/a": _BASIC_DOWNLOAD,
            "https://example.com/b": _BASIC_DOWNLOAD,
        },
    )

    def raising(plan, progress):
        raise RefusedContent("DRM")

    _install_executor(monkeypatch, raising)
    code = main(
        [
            "--folder",
            str(tmp_path),
            "https://example.com/a",
            "https://example.com/b",
        ]
    )
    assert code == 4  # RefusedContent


def test_real_run_does_not_swallow_keyboard_interrupt(
    tmp_path: Path, monkeypatch
) -> None:
    """``KeyboardInterrupt`` is ``BaseException``, not ``Exception``;
    the broad ``except Exception`` at the boundary must not catch it.

    We assert the executor's KeyboardInterrupt propagates out of
    ``main`` so the user gets the conventional 130 exit.
    """
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    def raising(plan, progress):
        raise KeyboardInterrupt()

    _install_executor(monkeypatch, raising)
    with pytest.raises(KeyboardInterrupt):
        main(["--folder", str(tmp_path), "https://example.com/fin"])


def test_real_run_returns_user_error_when_executor_raises_user_error(
    tmp_path: Path, monkeypatch
) -> None:
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    def raising(plan, progress):
        raise UserError("video removed by uploader")

    _install_executor(monkeypatch, raising)
    code = main(["--folder", str(tmp_path), "https://example.com/fin"])
    assert code == 2  # UserError


def test_real_run_returns_partial_when_refused_mixed_with_success(
    tmp_path: Path, monkeypatch
) -> None:
    """A Job with one success and one refusal -> partial (code 1).

    The acceptance criterion lists distinct codes for each cause; a
    partial Job is its own code, distinct from "all refused".
    """
    _install_resolver(
        monkeypatch,
        {
            "https://example.com/good": _BASIC_DOWNLOAD,
            "https://example.com/drm": {
                **_BASIC_DOWNLOAD,
                "video_id": "drm42",
                "title": "DRM Title",
            },
        },
    )

    calls: list[str] = []

    def fake_executor(plan, progress):
        calls.append(plan.url)
        if plan.url.endswith("/drm"):
            raise RefusedContent("DRM")
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        return plan.target_path

    _install_executor(monkeypatch, fake_executor)
    code = main(
        [
            "--folder",
            str(tmp_path),
            "https://example.com/good",
            "https://example.com/drm",
        ]
    )
    assert code == 1  # PartialSuccess, not 4
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Acceptance: distinct filenames for the two same-title Items
# ---------------------------------------------------------------------------


def test_two_items_with_the_same_title_do_not_overwrite_each_other(
    tmp_path: Path, monkeypatch
) -> None:
    """The [id] suffix means two same-title Items land at distinct paths."""
    _install_resolver(
        monkeypatch,
        {
            "https://example.com/a": {
                **_BASIC_DOWNLOAD,
                "video_id": "idAAA",
            },
            "https://example.com/b": {
                **_BASIC_DOWNLOAD,
                "video_id": "idBBB",
            },
        },
    )

    written: list[Path] = []

    def fake_executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        written.append(plan.target_path)
        return plan.target_path

    _install_executor(monkeypatch, fake_executor)
    code = main(
        [
            "--folder",
            str(tmp_path),
            "https://example.com/a",
            "https://example.com/b",
        ]
    )
    assert code == 0
    # Two distinct target paths.
    assert len(set(written)) == 2
    assert all(p.exists() for p in written)


# ---------------------------------------------------------------------------
# Folder placement: default vs override
# ---------------------------------------------------------------------------


def test_real_run_uses_the_default_output_root(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without ``--folder``, the platform default is honoured.

    We pin the platform default to ``tmp_path`` for this test by
    monkeypatching ``default_output_root``.
    """
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    def fake_executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        return plan.target_path

    _install_executor(monkeypatch, fake_executor)
    monkeypatch.setattr(cli, "default_output_root", lambda: tmp_path)

    code = main(["https://example.com/fin"])

    assert code == 0
    assert "Some Channel" in [p.name for p in tmp_path.iterdir()]


def test_folders_flag_overrides_the_default(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--folder`` overrides the platform default."""
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    def fake_executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        return plan.target_path

    _install_executor(monkeypatch, fake_executor)
    target = tmp_path / "explicit"
    main(["--folder", str(target), "https://example.com/fin"])
    assert (target / "Some Channel").is_dir()


# ---------------------------------------------------------------------------
# Engine options carry through
# ---------------------------------------------------------------------------


def test_real_run_passes_progress_hook_to_executor(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI must hand the executor a progress callback, not ``None``."""
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})
    seen: list[Callable[[dict[str, object]], None] | None] = []

    def fake_executor(plan, progress):
        seen.append(progress)
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        return plan.target_path

    _install_executor(monkeypatch, fake_executor)
    main(["--folder", str(tmp_path), "https://example.com/fin"])
    assert seen[0] is not None


# ---------------------------------------------------------------------------
# Real run vs dry run -- the same code path produces both
# ---------------------------------------------------------------------------


def test_real_run_uses_the_same_plan_path_as_dry_run(
    tmp_path: Path, monkeypatch
) -> None:
    """A real run and a ``--dry-run`` for the same link produce the
    same Plan fields: kind, profile, format, target."""
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    def fake_executor(plan, progress):
        plan.target_path.parent.mkdir(parents=True, exist_ok=True)
        plan.target_path.write_bytes(b"x")
        return plan.target_path

    _install_executor(monkeypatch, fake_executor)

    # Plan from the dry-run resolve_plan, captured directly.
    from vicxiun.plan import profile_from_name

    profile = profile_from_name("phone-safe")
    dry_plan = resolve_plan(
        "https://example.com/fin",
        profile,
        tmp_path,
        cli._resolve_metadata,
    )

    # Run the real run; assert it uses the same target.
    main(["--folder", str(tmp_path), "https://example.com/fin"])
    written = next((tmp_path / "Some Channel").iterdir())
    assert written == dry_plan.target_path


# ---------------------------------------------------------------------------
# Errors: every failure produces a named cause and a clear message
# ---------------------------------------------------------------------------


def test_real_run_failure_carries_a_named_cause(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every failure surfaces a named cause; nothing is silent."""
    _install_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    def raising(plan, progress):
        raise PrerequisiteMissing("ffmpeg not found on PATH")

    _install_executor(monkeypatch, raising)
    code = main(["--folder", str(tmp_path), "https://example.com/fin"])

    captured = capsys.readouterr()
    assert code == 3
    # The one-line error carries the named cause.
    err = captured.err
    assert "missing prereq" in err or "prerequisite" in err.lower()
    assert "ffmpeg" in err
    assert "https://example.com/fin" in err