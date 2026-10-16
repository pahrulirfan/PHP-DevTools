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
        return self.paths.bin / "fnm"

    @property
    def fnm_dir(self) -> Path:
        return self.paths.data / "fnm"

    def available(self) -> bool:
        return self.fnm_bin.exists()

    def install_fnm(self, fetch: Callable[[str], bytes], machine: str | None = None) -> Path:
        data = fetch(FNM_URL.format(asset=fnm_asset(machine)))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if "fnm" not in z.namelist():
                raise NodeError("fnm archive does not contain the 'fnm' binary")
            self.paths.bin.mkdir(parents=True, exist_ok=True)
            self.fnm_bin.write_bytes(z.read("fnm"))
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
        lines = [BEGIN, f'export PATH="{self.paths.bin}:$PATH"']
        if shell:
            lines += [f'export FNM_DIR="{self.node.fnm_dir}"',
                      f'eval "$({self.node.fnm_bin} env --use-on-cd --shell {shell})"']
        return "\n".join(lines + [END]) + "\n"

    def targets(self) -> list[tuple[Path, str | None]]:
        import shutil
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
                f.write_text(new)
            done.append(f)
        return done

    def remove(self) -> list[Path]:
        done = []
        for f, _ in self.targets():
            if f.exists() and BEGIN in f.read_text():
                f.write_text(self._strip(f.read_text()).strip("\n") + "\n")
                done.append(f)
        return done

    def installed(self) -> bool:
        f = self.home / ".bashrc"
        return f.exists() and BEGIN in f.read_text()
