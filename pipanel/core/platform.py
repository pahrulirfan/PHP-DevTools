"""Which operating system PIPanel runs on, and how it starts itself.

Linux runs every service as a systemd user unit. Windows and macOS have no systemd, so a small agent process
(`pipanel _agent`) supervises the services there and keeps them running when the window is closed.
PIPANEL_PLATFORM=linux|windows|macos overrides the detection (tests).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

LINUX, WINDOWS, MACOS = "linux", "windows", "macos"


def current() -> str:
    forced = os.environ.get("PIPANEL_PLATFORM")
    if forced in (LINUX, WINDOWS, MACOS):
        return forced
    if sys.platform.startswith("win"):
        return WINDOWS
    if sys.platform == "darwin":
        return MACOS
    return LINUX


def is_windows() -> bool:
    return current() == WINDOWS


def is_macos() -> bool:
    return current() == MACOS


def frozen() -> bool:
    """Running from a packaged executable (PyInstaller) rather than from Python."""
    return bool(getattr(sys, "frozen", False))


CLI_EXE = "pipanel-cli.exe"   # Windows package: a console build next to the windowed PIPanel.exe


def self_command() -> list[str]:
    """argv that runs PIPanel itself without a window: `<that> sync`, `<that> _agent`, ..."""
    if not frozen():
        return [sys.executable, "-m", "pipanel"]
    if is_windows():
        cli = Path(sys.executable).with_name(CLI_EXE)
        return [str(cli if cli.exists() else sys.executable)]
    return [sys.executable]


def gui_command() -> list[str]:
    """argv that opens the app (for login items)."""
    if frozen():
        return [sys.executable] if not is_windows() else [str(Path(sys.executable).with_name("PIPanel.exe"))]
    return [sys.executable, "-m", "pipanel"]


def exe(name: str) -> str:
    """File name of a program on this system (nginx -> nginx.exe on Windows)."""
    return f"{name}.exe" if is_windows() and not name.endswith(".exe") else name
