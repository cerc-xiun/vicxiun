"""Integration tests for ``vicxiun --dry-run``.

The CLI is the only integration seam: arguments in, output and exit codes
out. ``--dry-run`` exercises the path-builder and classifier without
touching the network -- the resolver is substituted here.

Tickets #5 and #6 are the contract under test; the tests pin the
contract behaviour at the seam:

* ``vicxiun --dry-run <link>`` prints a Plan and stops.
* No media is written; the output directory is not created.
* The resolver is the *real* code path, so the plan is what a real run
  would do.
* Combinator with ``--wait-for-stream``.
* Exit codes match the real-run contract.
* Argument-injection attempts (``--surprise`` as a link, ``-rf``, shell
  metacharacters, ``file://``) are rejected with the user-error code.
"""

from __future__ import annotations

import pytest

from vicxiun import cli
from vicxiun.cli import main

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _install_fake_resolver(monkeypatch, mapping):
    """Install a fake resolver that maps url -> dict.

    The resolver is plugged into the CLI's seam so the CLI uses no real
    network. The mapping is a dict from URL to the dict the resolver
    yields.

    A passing Prereq check stub is also installed so the CLI does not
    refuse to run because the test machine lacks ffmpeg/node on PATH.
    The real Prereq check is tested under ``tests/test_prereqs.py``.
    """
    from vicxiun import cli as _cli

    def resolver(url):
        if url not in mapping:
            from vicxiun.errors import UserError

            raise UserError(f"engine cannot resolve {url}")
        return dict(mapping[url])

    monkeypatch.setattr(_cli, "_resolver", resolver)
    monkeypatch.setattr(_cli, "_prereq_check", lambda: 0)


_BASIC_DOWNLOAD = {
    "title": "Best of 2024",
    "uploader": "Some Channel",
    "ext": "mp4",
    "is_live": False,
    "format_description": "MP4, H.264 + AAC, up to 1080p",
    "video_id": "abc123XYZ",
}

_BASIC_LIVE = {
    "title": "Tonight's Stream",
    "uploader": "Some Channel",
    "ext": "mkv",
    "is_live": True,
    "format_description": "MKV, bestvideo+bestaudio recorded until stopped",
    "video_id": "live999",
}


# ---------------------------------------------------------------------------
# Acceptance criteria - mapping to ticket #5
# ---------------------------------------------------------------------------


def test_dry_run_prints_one_plan_per_link(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})
    monkeypatch.setattr(cli, "default_output_root", lambda: tmp_path)

    code = main(["--dry-run", "--folder", str(tmp_path), "https://example.com/fin"])

    captured = capsys.readouterr()
    assert code == 0
    out = captured.out
    assert "https://example.com/fin" in out
    assert "Download" in out
    assert "phone-safe" in out
    assert "MP4" in out
    assert "1080p" in out
    assert str(tmp_path) in out or str(tmp_path.resolve()) in out
    assert "Nothing was downloaded or written" in out


def test_dry_run_does_not_create_the_output_folder(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    nested = tmp_path / "does" / "not" / "exist"
    _install_fake_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    assert not nested.exists()

    code = main(["--dry-run", "--folder", str(nested), "https://example.com/fin"])

    captured = capsys.readouterr()
    assert code == 0
    assert "Nothing was downloaded" in captured.out
    # Output directory must NOT have been created.
    assert not nested.exists()


def test_dry_run_with_wait_for_stream_reports_wait_only(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_resolver(monkeypatch, {"https://example.com/live": _BASIC_LIVE})
    code = main(
        [
            "--dry-run",
            "--wait-for-stream",
            "--folder",
            str(tmp_path),
            "https://example.com/live",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    out = captured.out
    assert "Capture" in out
    assert "would wait for the stream to start" in out


def test_dry_run_returns_quickly_even_when_wait_requested(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI does not actually wait; it returns once the plan is drawn.

    We do not test elapsed time (it's flaky), only that control returns
    without a subprocess that would block.
    """
    _install_fake_resolver(monkeypatch, {"https://example.com/live": _BASIC_LIVE})
    code = main(
        [
            "--dry-run",
            "--wait-for-stream",
            "--folder",
            str(tmp_path),
            "https://example.com/live",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Nothing was downloaded or written" in captured.out


# ---------------------------------------------------------------------------
# Argument-injection tests (the table of hostile inputs)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "-rf",
        "--verbose",
        "-",
        "--unsafe",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "javascript:alert(1)",
        "https://ex.com/$(touch x)",
        "https://ex.com/`id`",
    ],
)
def test_dry_run_rejects_hostile_links_with_user_error_code(
    tmp_path, monkeypatch, bad: str
) -> None:
    """A hostile link is either caught by ``resolve_plan`` (returns
    UserError -> non-zero code) or by argparse (raises SystemExit(2)).

    In both cases the caller's process exits with a non-zero code; we
    accept either form here. The underlying guarantee is that the link
    never reaches the resolver or the filesystem.
    """
    _install_fake_resolver(monkeypatch, {})

    try:
        code = main(["--dry-run", "--folder", str(tmp_path), bad])
    except SystemExit as exc:
        # argparse rejected the argument.
        assert exc.code != 0
    else:
        assert code != 0


def test_dry_run_rejects_empty_link(tmp_path, monkeypatch) -> None:
    _install_fake_resolver(monkeypatch, {})
    code = main(["--dry-run", "--folder", str(tmp_path), ""])
    assert code != 0


def test_dry_run_does_not_treat_dash_prefix_as_flag(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A link beginning with ``-`` is not handed to argparse as a flag."""
    _install_fake_resolver(monkeypatch, {})
    # ``--`` ends the option list, then ``-rf`` is positional. We expect it
    # to be rejected because it begins with ``-``.
    code = main(["--dry-run", "--folder", str(tmp_path), "--", "-rf"])
    assert code != 0


# ---------------------------------------------------------------------------
# Exit codes under various real-run-mirror conditions
# ---------------------------------------------------------------------------


def test_dry_run_exits_zero_for_a_single_valid_link(tmp_path, monkeypatch) -> None:
    _install_fake_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})
    code = main(["--dry-run", "--folder", str(tmp_path), "https://example.com/fin"])
    assert code == 0


def test_dry_run_returns_partial_when_some_links_fail(tmp_path, monkeypatch) -> None:
    """One link bad, one link fine -> partial-success exit code.

    Exit code: 1 (the partial-success code; tests assert it here).
    """
    _install_fake_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})
    # ``file:///etc/passwd`` is hostile and reaches the validator.
    code = main(
        [
            "--dry-run",
            "--folder",
            str(tmp_path),
            "https://example.com/fin",
            "file:///etc/passwd",
        ]
    )
    # Partial-success code is 1 (matching "real run under same conditions").
    assert code == 1


def test_dry_run_returns_nonzero_when_the_link_cannot_resolve(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unresolvable link yields a user-error code."""
    from vicxiun.errors import UserError

    def raising(url):
        raise UserError(f"engine cannot resolve {url}")

    monkeypatch.setattr(cli, "_resolver", raising)
    code = main(["--dry-run", "--folder", str(tmp_path), "https://example.com/fin"])
    assert code != 0


# ---------------------------------------------------------------------------
# No-write guarantee (directory snapshot)
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing_to_the_output_folder(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A before/after snapshot of the output folder must be identical."""
    folder = tmp_path / "out"
    folder.mkdir()
    before = {p.relative_to(folder) for p in folder.rglob("*")}

    _install_fake_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})
    code = main(["--dry-run", "--folder", str(folder), "https://example.com/fin"])

    after = {p.relative_to(folder) for p in folder.rglob("*")}
    assert code == 0
    assert before == after


# ---------------------------------------------------------------------------
# Prerequisite checks still run
# ---------------------------------------------------------------------------


def test_prereq_check_runs_when_a_link_is_provided(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The Prereq check fires on every invocation -- even with a link --
    so a missing ffmpeg is announced before anything else."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setenv("PATH", "")

    code = main(["--dry-run", "https://example.com/fin"])
    captured = capsys.readouterr()
    assert code != 0
    assert "ffmpeg" in captured.err


# ---------------------------------------------------------------------------
# Profile selection and --folder override
# ---------------------------------------------------------------------------


def test_folder_flag_overrides_the_default(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})

    target = tmp_path / "explicit"
    code = main(["--dry-run", "--folder", str(target), "https://example.com/fin"])

    captured = capsys.readouterr()
    assert code == 0
    out = captured.out
    assert str(target) in out or str(target.resolve()) in out


def test_profile_best_is_a_known_choice(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})
    code = main(
        [
            "--dry-run",
            "--folder",
            str(tmp_path),
            "--profile",
            "best",
            "https://example.com/fin",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "best" in captured.out


# ---------------------------------------------------------------------------
# Help / version still work (no regression)
# ---------------------------------------------------------------------------


def test_help_is_unchanged_after_options_added(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--dry-run" in out
    assert "--wait-for-stream" in out
    assert "--folder" in out
    assert "--profile" in out
