"""Node.js through fnm, plus shell integration that can be removed cleanly."""
from __future__ import annotations

import io
import platform
import re
import zipfile
from pathlib import Path
from typing import Callable

from .paths import Paths
from .runner import Result, Runner

FNM_URL = "https://github.com/Schniz/fnm/releases/latest/download/{asset}.zip"
BEGIN, END = "# >>> pipanel >>>", "# <<< pipanel <<<"
_VERSION_RE = re.compile(r"^(lts-latest|latest|v?\d+(\.\d+){0,2})$")


class NodeError(Exception):
    pass


def fnm_asset(machine: str | None = None) -> str:
    from . import platform as plat
    if plat.is_windows():
        return "fnm-windows"
    if plat.is_macos():
        return "fnm-macos"
    m = (machine or platform.machine()).lower()
    if m in ("aarch64", "arm64"):
        return "fnm-arm64"
    if m in ("x86_64", "amd64"):
        return "fnm-linux"
    raise NodeError(f"unsupported architecture: {m}")


def parse_list(text: str) -> list[tuple[str, bool]]:
    """Output of `fnm list` -> [(version, is_default)]."""
    out = []
    for line in text.splitlines():
        m = re.search(r"\bv(\d+\.\d+\.\d+)\b", line)
        if m:
            out.append((m.group(1), "default" in line))
    return out


def parse_remote(text: str, per_major: int = 3, majors: int = 6) -> list[str]:
    """Latest versions of each major (only the newest `majors` majors)."""
    by: dict[int, list[tuple[int, int, int]]] = {}
    for m in re.finditer(r"\bv(\d+)\.(\d+)\.(\d+)\b", text):
        a, b, c = map(int, m.groups())
        by.setdefault(a, []).append((a, b, c))
    out = []
    for major in sorted(by, reverse=True)[:majors]:
        for v in sorted(set(by[major]), reverse=True)[:per_major]:
            out.append(".".join(map(str, v)))
    return out


class Node:
    def __init__(self, paths: Paths, runner: Runner):
        self.paths, self.runner = paths, runner

    @property
    def fnm_bin(self) -> Path:
        from . import platform as plat
        return self.paths.bin / plat.exe("fnm")

    @property
    def fnm_dir(self) -> Path:
        return self.paths.data / "fnm"

    def available(self) -> bool:
        return self.fnm_bin.exists()

    def install_fnm(self, fetch: Callable[[str], bytes], machine: str | None = None) -> Path:
        data = fetch(FNM_URL.format(asset=fnm_asset(machine)))
        name = self.fnm_bin.name
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            found = next((n for n in z.namelist() if Path(n).name == name), None)
            if found is None:
                raise NodeError(f"fnm archive does not contain the '{name}' binary")
            self.paths.bin.mkdir(parents=True, exist_ok=True)
            self.fnm_bin.write_bytes(z.read(found))
        self.fnm_bin.chmod(0o755)
        return self.fnm_bin

    def _fnm(self, *args: str, timeout: float = 600) -> Result:
        return self.runner.run([str(self.fnm_bin), *args], env={"FNM_DIR": str(self.fnm_dir)}, timeout=timeout)

    @staticmethod
    def _check(v: str) -> str:
        if not _VERSION_RE.match(v):
            raise NodeError(f"invalid version: {v!r}")
        return v

    def installed(self) -> list[tuple[str, bool]]:
        return parse_list(self._fnm("list").out)

    def remote(self) -> list[str]:
        r = self._fnm("list-remote")
        if not r.ok:
            raise NodeError(r.text)
        return parse_remote(r.out)

    def install(self, v: str) -> Result: return self._fnm("install", self._check(v))
    def uninstall(self, v: str) -> Result: return self._fnm("uninstall", self._check(v))
    def set_default(self, v: str) -> Result: return self._fnm("default", self._check(v))


# ---------- integrasi shell ----------
class ShellIntegration:
    def __init__(self, paths: Paths, home: Path, node: Node):
        self.paths, self.home, self.node = paths, home, node

    def _block(self, shell: str | None) -> str:
        if shell == "powershell":
            return "\n".join([BEGIN, f'$env:Path = "{self.paths.bin};" + $env:Path',
                              f'$env:FNM_DIR = "{self.node.fnm_dir}"',
                              f'if (Test-Path "{self.node.fnm_bin}") {{ & "{self.node.fnm_bin}" env --use-on-cd '
                              f'--shell powershell | Out-String | Invoke-Expression }}', END]) + "\n"
        lines = [BEGIN, f'export PATH="{self.paths.bin}:$PATH"']
        if shell:
            lines += [f'export FNM_DIR="{self.node.fnm_dir}"',
                      f'eval "$({self.node.fnm_bin} env --use-on-cd --shell {shell})"']
        return "\n".join(lines + [END]) + "\n"

    def targets(self) -> list[tuple[Path, str | None]]:
        import shutil
        from . import platform as plat
        if plat.is_windows():   # Windows PowerShell 5 and PowerShell 7 profiles
            docs = self.home / "Documents"
            return [(docs / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1", "powershell"),
                    (docs / "PowerShell" / "Microsoft.PowerShell_profile.ps1", "powershell")]
        if plat.is_macos():     # zsh is the default shell; bash reads .bash_profile
            return [(self.home / ".zshrc", "zsh"), (self.home / ".bash_profile", "bash")]
        t: list[tuple[Path, str | None]] = [(self.home / ".bashrc", "bash"), (self.home / ".profile", None)]
        zrc = self.home / ".zshrc"
        if zrc.exists() or shutil.which("zsh"):
            t.append((zrc, "zsh"))
        return t

    @staticmethod
    def _strip(text: str) -> str:
        return re.sub(rf"\n?{re.escape(BEGIN)}.*?{re.escape(END)}\n?", "\n", text, flags=re.S)

    def install(self) -> list[Path]:
        done = []
        for f, shell in self.targets():
            text = f.read_text() if f.exists() else ""
            base = self._strip(text).rstrip("\n")
            new = (base + "\n\n" if base else "") + self._block(shell)
            if new != text:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(new)
            done.append(f)
        self._user_path(True)
        return done

    def _user_path(self, add: bool) -> None:
        """Windows: also put PIPanel's bin folder on the user's PATH, so cmd.exe, VS Code and every new program
        find `php`, `composer` and `pipanel`."""
        from . import platform as plat
        if not plat.is_windows():
            return
        import ctypes
        import winreg
        bin_dir = str(self.paths.bin)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as k:
            try:
                value, kind = winreg.QueryValueEx(k, "Path")
            except FileNotFoundError:
                value, kind = "", winreg.REG_EXPAND_SZ
            parts = [x for x in value.split(";") if x and x.rstrip("\\") != bin_dir.rstrip("\\")]
            parts = [bin_dir, *parts] if add else parts
            winreg.SetValueEx(k, "Path", 0, kind, ";".join(parts))
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000, None)

    def remove(self) -> list[Path]:
        done = []
        for f, _ in self.targets():
            if f.exists() and BEGIN in f.read_text():
                f.write_text(self._strip(f.read_text()).strip("\n") + "\n")
                done.append(f)
        self._user_path(False)
        return done

    def installed(self) -> bool:
        f = self.targets()[0][0]
        return f.exists() and BEGIN in f.read_text()
