"""PHP on macOS: Homebrew builds (the shivammathur/php tap has every version side by side), run as PHP-FPM like on
Linux. Extension ini files live in Homebrew's etc/php/<version>/conf.d; PIPanel links them into its own conf.d."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .paths import Paths
from .php import PHP, PHPError, SUPPORTED
from .runner import Result, Runner

TAP = "shivammathur/php"


def brew_prefix(runner: Runner | None = None) -> Path:
    for p in ("/opt/homebrew", "/usr/local"):   # Apple silicon, Intel
        if (Path(p) / "bin" / "brew").exists():
            return Path(p)
    brew = shutil.which("brew")
    return Path(brew).resolve().parent.parent if brew else Path("/opt/homebrew")


class BrewPHP(PHP):
    def __init__(self, paths: Paths, prefix: Path | None = None):
        self.prefix = prefix or brew_prefix()
        super().__init__(paths, etc_php=self.prefix / "etc" / "php", sbin=self.prefix / "sbin",
                         usr_bin=self.prefix / "bin")

    def keg(self, v: str) -> Path:
        return self.prefix / "opt" / f"php@{v}"

    def installed(self) -> list[str]:
        vs = [v for v in SUPPORTED if (self.keg(v) / "sbin" / "php-fpm").exists()]
        return sorted(vs, key=lambda v: tuple(map(int, v.split("."))))

    def fpm_bin(self, v: str) -> Path:
        return self.keg(v) / "sbin" / "php-fpm"

    def cli_bin(self, v: str) -> Path:
        return self.keg(v) / "bin" / "php"

    def cli_template(self) -> str:
        return f"{self.prefix}/opt/php@$v/bin/php"

    def install(self, v: str, runner: Runner) -> Result:
        r = runner.run(["brew", "tap", TAP], timeout=1800)
        if not r.ok:
            return r
        return runner.run(["brew", "install", f"{TAP}/php@{v}"], timeout=3600)

    def uninstall(self, v: str, runner: Runner) -> Result:
        return runner.run(["brew", "uninstall", f"{TAP}/php@{v}"], timeout=1800)

    # --- extensions: Homebrew's conf.d holds ext-<name>.ini files ---
    def _mods(self, v: str) -> Path:
        return self.etc_php / v / "conf.d"

    def ensure_conf_d(self, v: str, settings: dict[str, str] | None = None) -> None:
        d = self.conf_d(v)
        d.mkdir(parents=True, exist_ok=True)
        marker = d / ".seeded"
        if not marker.exists():
            src = self._mods(v)
            if src.is_dir():
                for ini in sorted(src.glob("*.ini")):
                    dst = d / ini.name
                    if not dst.exists() and not dst.is_symlink():
                        dst.symlink_to(ini.resolve())
            marker.write_text("")
        self.write_settings(v, settings or {})

    @staticmethod
    def _name(ini: Path) -> str:
        return re.sub(r"^(ext-|\d+-)", "", ini.stem)

    def _enabled_link(self, v: str, name: str) -> Path | None:
        for f in self.conf_d(v).glob("*.ini"):
            if f.is_symlink() and self._name(f) == name:
                return f
        return None

    def extensions(self, v: str) -> list[tuple[str, bool]]:
        mods = self._mods(v)
        names = sorted({self._name(p) for p in mods.glob("*.ini")}) if mods.is_dir() else []
        return [(n, self._enabled_link(v, n) is not None) for n in names]

    def set_extension(self, v: str, name: str, enabled: bool) -> None:
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise PHPError(f"invalid extension name: {name!r}")
        existing = self._enabled_link(v, name)
        if enabled and existing is None:
            src = next((p for p in self._mods(v).glob("*.ini") if self._name(p) == name), None)
            if src is None:
                raise PHPError(f"extension {name} is not installed for PHP {v}")
            (self.conf_d(v) / src.name).symlink_to(src.resolve())
        elif not enabled and existing is not None:
            existing.unlink()

    def install_extension(self, v: str, name: str, runner: Runner) -> Result:
        """Extra extensions come from shivammathur/extensions (xdebug@8.3, redis@8.3, ...)."""
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise PHPError(f"invalid extension name: {name!r}")
        r = runner.run(["brew", "tap", "shivammathur/extensions"], timeout=1800)
        return r if not r.ok else runner.run(["brew", "install", f"shivammathur/extensions/{name}@{v}"], timeout=3600)
