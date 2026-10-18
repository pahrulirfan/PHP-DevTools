"""Open a site in the browser, file manager, editor, or terminal."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import platform

TERMINALS = ("gnome-terminal", "x-terminal-emulator", "xfce4-terminal", "konsole", "xterm")


def terminal_cmd(path: Path, which=shutil.which) -> list[str] | None:
    if platform.is_windows():
        if which("wt"):   # Windows Terminal
            return ["wt", "-d", str(path)]
        return ["cmd", "/c", "start", "cmd", "/k", f'cd /d "{path}"']
    if platform.is_macos():
        return ["open", "-a", "Terminal", str(path)]
    for t in TERMINALS:
        if which(t):
            if t == "gnome-terminal":
                return [t, f"--working-directory={path}"]
            if t in ("xfce4-terminal",):
                return [t, f"--working-directory={path}"]
            if t == "konsole":
                return [t, "--workdir", str(path)]
            return [t]   # cwd is set via Popen
    return None


def _spawn(argv: list[str], cwd: Path | None = None) -> bool:
    kw: dict = dict(cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if platform.is_windows():
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS, NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen(argv, **kw)
        return True
    except OSError:
        return False


def _open(target: str) -> bool:
    if platform.is_windows():
        try:
            os.startfile(target)   # type: ignore[attr-defined]  (Windows only)
            return True
        except OSError:
            return False
    return _spawn(["open" if platform.is_macos() else "xdg-open", target])


def open_url(url: str) -> bool: return _open(url)
def open_folder(path: Path) -> bool: return _open(str(path))


def open_vscode(path: Path) -> bool:
    code = shutil.which("code") or shutil.which("code.cmd") or "code"
    if platform.is_windows() and code.lower().endswith(".cmd"):
        return _spawn(["cmd", "/c", code, str(path)])
    return _spawn([code, str(path)])


def open_terminal(path: Path) -> bool:
    cmd = terminal_cmd(path)
    return bool(cmd) and _spawn(cmd, cwd=path)
