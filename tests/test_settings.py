"""Integration tests for ``vicxiun settings`` (ticket #16).

Choices are made once and remembered, and a one-off never changes
them. The CLI is the only integration seam: arguments in, output,
exit codes and the settings file on disk out. The autouse fixture in
``conftest.py`` redirects the settings file into a temporary
directory, so no test touches the real one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vicxiun import cli
from vicxiun.cli import main

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


def _install_fake_resolver(monkeypatch, mapping):
    """Install a fake resolver and a passing Prereq check stub."""

    def resolver(url):
        if url not in mapping:
            from vicxiun.errors import UserError

            raise UserError(f"engine cannot resolve {url}")
        return dict(mapping[url])

    monkeypatch.setattr(cli, "_resolver", resolver)
    monkeypatch.setattr(cli, "_prereq_check", lambda: 0)


def _settings_file() -> Path:
    """The redirected settings file path the autouse fixture installed."""
    return cli.settings_path()


# ---------------------------------------------------------------------------
# Showing settings
# ---------------------------------------------------------------------------


def test_settings_shows_built_in_defaults_and_the_file_path(capsys) -> None:
    code = main(["settings"])

    captured = capsys.readouterr()
    assert code == 0
    out = captured.out
    assert "profile = phone-safe" in out
    assert "built-in default" in out
    assert "folder" in out
    assert f"Settings file: {cli.settings_path()}" in out
    # Showing settings must not create the file.
    assert not os.path.exists(str(_settings_file()))


def test_settings_shows_one_setting(capsys) -> None:
    code = main(["settings", "profile"])

    captured = capsys.readouterr()
    assert code == 0
    assert "profile = phone-safe" in captured.out


def test_settings_help_prints_usage(capsys) -> None:
    code = main(["settings", "--help"])

    captured = capsys.readouterr()
    assert code == 0
    assert "usage: vicxiun settings" in captured.out
    assert "profile" in captured.out


def test_main_help_mentions_the_settings_command(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0
    assert "settings" in capsys.readouterr().out


def test_unknown_key_is_a_user_error(capsys) -> None:
    code = main(["settings", "colour"])

    captured = capsys.readouterr()
    assert code == 2
    assert "unknown setting" in captured.err
    assert "profile" in captured.err  # names the known keys


def test_saving_an_unknown_profile_is_refused_and_saves_nothing(capsys) -> None:
    code = main(["settings", "profile", "4k-ultra"])

    captured = capsys.readouterr()
    assert code == 2
    assert "error" in captured.err
    assert not os.path.exists(str(_settings_file()))


# ---------------------------------------------------------------------------
# Changing settings
# ---------------------------------------------------------------------------


def test_saving_writes_human_readable_json(capsys) -> None:
    code = main(["settings", "profile", "best"])

    captured = capsys.readouterr()
    assert code == 0
    assert "Saved: profile = best" in captured.out

    text = _settings_file().read_text(encoding="utf-8")
    assert "\n" in text  # indented, hand-editable, not one line
    assert json.loads(text) == {"profile": "best"}


def test_saving_the_folder_is_remembered(capsys) -> None:
    code = main(["settings", "folder", str(_settings_file().parent / "out")])

    assert code == 0
    text = _settings_file().read_text(encoding="utf-8")
    assert json.loads(text) == {"folder": str(_settings_file().parent / "out")}


# ---------------------------------------------------------------------------
# Saved values are used; flags override for one run only
# ---------------------------------------------------------------------------


def test_saved_profile_is_used_when_no_flag_is_given(monkeypatch, capsys) -> None:
    _install_fake_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})
    assert main(["settings", "profile", "best"]) == 0

    code = main(["--dry-run", "https://example.com/fin"])

    captured = capsys.readouterr()
    assert code == 0
    assert "best" in captured.out


def test_saved_folder_is_used_when_no_flag_is_given(
    tmp_path, monkeypatch, capsys
) -> None:
    _install_fake_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})
    target = tmp_path / "saved-place"
    assert main(["settings", "folder", str(target)]) == 0

    code = main(["--dry-run", "https://example.com/fin"])

    captured = capsys.readouterr()
    assert code == 0
    assert str(target) in captured.out or str(target.resolve()) in captured.out


def test_a_flag_overrides_the_saved_value_for_that_run_only(
    monkeypatch, capsys
) -> None:
    _install_fake_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})
    assert main(["settings", "profile", "best"]) == 0

    code = main(["--dry-run", "--profile", "audio", "https://example.com/fin"])

    captured = capsys.readouterr()
    assert code == 0
    assert "audio" in captured.out
    # The one-off never changes the saved value.
    assert json.loads(_settings_file().read_text(encoding="utf-8")) == {
        "profile": "best"
    }

    captured = capsys.readouterr()
    code = main(["--dry-run", "https://example.com/fin"])
    assert code == 0
    assert "best" in capsys.readouterr().out


def test_a_folder_flag_does_not_persist(tmp_path, monkeypatch, capsys) -> None:
    _install_fake_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})
    saved = tmp_path / "saved"
    oneshot = tmp_path / "oneshot"
    assert main(["settings", "folder", str(saved)]) == 0

    code = main(["--dry-run", "--folder", str(oneshot), "https://example.com/fin"])

    captured = capsys.readouterr()
    assert code == 0
    assert str(oneshot) in captured.out
    assert json.loads(_settings_file().read_text(encoding="utf-8")) == {
        "folder": str(saved)
    }


# ---------------------------------------------------------------------------
# A corrupt or unreadable settings file warns instead of crashing
# ---------------------------------------------------------------------------


def test_corrupt_json_is_ignored_with_a_clear_message(monkeypatch, capsys) -> None:
    _install_fake_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})
    _settings_file().write_text("{not json", encoding="utf-8")

    code = main(["--dry-run", "https://example.com/fin"])

    captured = capsys.readouterr()
    assert code == 0
    assert "Ignoring corrupt settings file" in captured.err
    assert "phone-safe" in captured.out  # built-in default was used


def test_settings_file_that_is_not_an_object_is_ignored(capsys) -> None:
    _settings_file().write_text('["a list"]', encoding="utf-8")

    code = main(["settings"])

    captured = capsys.readouterr()
    assert code == 0
    assert "Ignoring corrupt settings file" in captured.err
    assert "profile = phone-safe" in captured.out


def test_unreadable_settings_file_is_ignored_with_a_clear_message(capsys) -> None:
    # A directory where the file should be is unreadable as JSON on
    # every platform, and exercises the OSError branch.
    path = _settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()

    code = main(["settings"])

    captured = capsys.readouterr()
    assert code == 0
    assert "Ignoring unreadable settings file" in captured.err


def test_a_hand_edited_unknown_profile_falls_back_with_a_warning(
    monkeypatch, capsys
) -> None:
    _install_fake_resolver(monkeypatch, {"https://example.com/fin": _BASIC_DOWNLOAD})
    _settings_file().write_text('{"profile": "ultra4k"}', encoding="utf-8")

    code = main(["--dry-run", "https://example.com/fin"])

    captured = capsys.readouterr()
    assert code == 0
    assert "Ignoring unknown profile" in captured.err
    assert "phone-safe" in captured.out


# ---------------------------------------------------------------------------
# The settings file lives in the platform's configuration location
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="Windows-only location")
def test_windows_settings_path_uses_appdata(tmp_path, monkeypatch) -> None:
    from vicxiun.platform import settings_path as real_settings_path

    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert real_settings_path() == tmp_path / "vicxiun" / "settings.json"


@pytest.mark.skipif(os.name == "nt", reason="XDG-only location")
def test_posix_settings_path_honours_xdg_config_home(tmp_path, monkeypatch) -> None:
    from vicxiun.platform import settings_path as real_settings_path

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert real_settings_path() == tmp_path / "vicxiun" / "settings.json"
