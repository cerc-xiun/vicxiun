"""Platform-specific defaults.

A single, narrow module. ``default_output_root`` and ``settings_path``
are the only functions the CLI calls at present; future defaults
(Termux behaviour tweaks) will land here too.
"""

from __future__ import annotations

import os
from pathlib import Path

from vicxiun.prereqs import detect_platform


def default_output_root() -> Path:
    """Return the platform's default folder for finished Items."""
    info = detect_platform()
    if info.is_termux:
        # Android shared storage: a folder the gallery and file
        # manager will see.
        return Path("/sdcard/Movies/vicxiun")
    home = Path(os.path.expanduser("~"))
    return home / "Videos" / "vicxiun"


def settings_path() -> Path:
    """Return the settings file in the platform's configuration location.

    Windows: ``%APPDATA%\\vicxiun\\settings.json``. macOS, Linux and
    Termux: the XDG convention, ``~/.config/vicxiun/settings.json``
    (honouring ``XDG_CONFIG_HOME``). Deliberately not next to the
    downloaded Items: settings are configuration, not output.
    """
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "vicxiun" / "settings.json"
        home = Path(os.path.expanduser("~"))
        return home / "AppData" / "Roaming" / "vicxiun" / "settings.json"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "vicxiun" / "settings.json"
    home = Path(os.path.expanduser("~"))
    return home / ".config" / "vicxiun" / "settings.json"
