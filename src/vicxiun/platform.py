"""Platform-specific defaults.

A single, narrow module. ``default_output_root`` is the only function
the CLI calls at present; future defaults (settings path, Termux
behaviour) will land here too.
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
