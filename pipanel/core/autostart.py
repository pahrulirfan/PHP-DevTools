"""Start the app at login (XDG autostart)."""
from __future__ import annotations

import sys
from pathlib import Path

ENTRY = """[Desktop Entry]
Type=Application
Name=PIPanel
Comment=Local PHP development environment
Exec={exec}
Icon=pipanel
Terminal=false
X-GNOME-Autostart-enabled=true
"""


def entry_path(config_home: Path) -> Path:
    return config_home / "autostart" / "pipanel.desktop"


def enable(config_home: Path, exec_cmd: str | None = None) -> Path:
    p = entry_path(config_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(ENTRY.format(exec=exec_cmd or f"{sys.executable} -m pipanel --minimized"))
    return p


def disable(config_home: Path) -> None:
    entry_path(config_home).unlink(missing_ok=True)


def enabled(config_home: Path) -> bool:
    return entry_path(config_home).exists()
