"""Start the app (and, on Windows and macOS, the service agent) at login.

Linux: an XDG autostart entry. Windows: the user's Run key in the registry. macOS: a LaunchAgent.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import platform

ENTRY = """[Desktop Entry]
Type=Application
Name=PIPanel
Comment=Local PHP development environment
Exec={exec}
Icon=pipanel
Terminal=false
X-GNOME-Autostart-enabled=true
"""
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>RunAtLoad</key><true/>
    <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
"""
APP, AGENT = "app", "agent"
NAMES = {APP: ("PIPanel", "com.pipanel.app"), AGENT: ("PIPanel services", "com.pipanel.agent")}


def entry_path(config_home: Path) -> Path:
    return config_home / "autostart" / "pipanel.desktop"


def _argv(what: str) -> list[str]:
    cmd = platform.gui_command() if what == APP else platform.self_command()
    if platform.is_windows() and not platform.frozen():   # no console window at login
        cmd = [str(Path(sys.executable).with_name("pythonw.exe")), *cmd[1:]]
    return cmd + (["--minimized"] if what == APP else ["_agent"])


def _plist(what: str, home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{NAMES[what][1]}.plist"


def _win_set(name: str, value: str | None) -> None:
    import winreg   # Windows only
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
        if value is None:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def _win_get(name: str) -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def set_login(what: str, on: bool, home: Path | None = None) -> None:
    """Windows and macOS: start the app or the agent at login."""
    argv = _argv(what)
    if platform.is_windows():
        _win_set(NAMES[what][0], " ".join(f'"{a}"' for a in argv) if on else None)
    elif platform.is_macos():
        f = _plist(what, home)
        if on:
            from xml.sax.saxutils import escape
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(PLIST.format(label=NAMES[what][1],
                                      args="\n".join(f"        <string>{escape(a)}</string>" for a in argv)))
        else:
            f.unlink(missing_ok=True)


def login_enabled(what: str, home: Path | None = None) -> bool:
    if platform.is_windows():
        return _win_get(NAMES[what][0]) is not None
    if platform.is_macos():
        return _plist(what, home).exists()
    return False


def enable(config_home: Path, exec_cmd: str | None = None) -> Path:
    if platform.current() != platform.LINUX:
        set_login(APP, True)
        return config_home
    p = entry_path(config_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(ENTRY.format(exec=exec_cmd or f"{sys.executable} -m pipanel --minimized"))
    return p


def disable(config_home: Path) -> None:
    if platform.current() != platform.LINUX:
        set_login(APP, False)
        return
    entry_path(config_home).unlink(missing_ok=True)


def enabled(config_home: Path) -> bool:
    if platform.current() != platform.LINUX:
        return login_enabled(APP)
    return entry_path(config_home).exists()
