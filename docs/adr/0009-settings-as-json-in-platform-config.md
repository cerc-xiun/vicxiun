# Settings as hand-editable JSON in the platform config location

Saved settings live in one JSON file at the platform's configuration
location — `%APPDATA%\vicxiun\settings.json` on Windows, the XDG
convention (`~/.config/vicxiun/settings.json`) elsewhere, Termux
included. No directories library is added: platform detection is
already required, and the logic is a handful of lines. The file is
indented, sorted, and safe to edit by hand; a corrupt or unreadable
file is named in a warning and the built-in defaults are used, so a
hand-edit can never crash a run.

**_Rejected_**: TOML. The stdlib reads it only from Python 3.11 (the
project floor is 3.10) and cannot write it at all, so it would cost a
dependency for no gain at this file size.

**_Rejected_**: keeping the file inside the output folder. Settings are
configuration, not output; the user may point the output folder
anywhere, including a removable drive the settings file must not depend
on.
