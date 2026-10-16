"""Open a site in the browser, file manager, editor, or terminal."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

TERMINALS = ("gnome-terminal", "x-terminal-emulator", "xfce4-terminal", "konsole", "xterm")


def terminal_cmd(path: Path, which=shutil.which) -> list[str] | None:
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
    try:
        subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def open_url(url: str) -> bool: return _spawn(["xdg-open", url])
def open_folder(path: Path) -> bool: return _spawn(["xdg-open", str(path)])
def open_vscode(path: Path) -> bool: return _spawn(["code", str(path)])


def open_terminal(path: Path) -> bool:
    cmd = terminal_cmd(path)
    return bool(cmd) and _spawn(cmd, cwd=path)
