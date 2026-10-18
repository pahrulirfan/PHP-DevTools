"""PHP on Windows: the official builds from windows.php.net, unpacked per version into PIPanel's own folder.

There is no PHP-FPM on Windows, so each version runs as a small pool of php-cgi.exe processes (see fcgipool.py),
and extensions are DLLs that PIPanel enables with an ini file in its own conf.d (no admin rights needed).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from . import platform
from .archive import member, unpack
from .config import PHP_DEFAULTS
from .paths import Paths
from .php import COMPOSER_URL, PHP, PHPError, SUPPORTED

RELEASES = "https://downloads.php.net/~windows/releases/releases.json"
DOWNLOADS = "https://downloads.php.net/~windows/releases/"
PECL = "https://downloads.php.net/~windows/pecl/releases/"
XDEBUG_PAGE = "https://xdebug.org/download"
WORKERS = 4
# enabled the first time a version is set up: what Laravel and Composer need, plus the usual database drivers
DEFAULT_EXTENSIONS = ("curl", "fileinfo", "mbstring", "openssl", "pdo_mysql", "pdo_sqlite", "sqlite3", "mysqli",
                      "zip", "intl", "gd", "exif", "sodium", "bcmath", "opcache")
ZEND = ("opcache", "xdebug")   # loaded with zend_extension=


def base_port(v: str) -> int:
    major, minor = v.split(".")
    return 9000 + int(major) * 100 + int(minor) * 10   # 8.3 -> 9830..9833


class WindowsPHP(PHP):
    reload_how = None            # a php-cgi pool is restarted, not reloaded
    uses_fpm_conf = False

    def __init__(self, paths: Paths, root: Path | None = None):
        super().__init__(paths)
        self.root = root or paths.data / "stack" / "php"

    # --- versions ---
    def installed(self) -> list[str]:
        vs = [v for v in SUPPORTED if (self.root / v / "php-cgi.exe").exists()]
        return sorted(vs, key=lambda v: tuple(map(int, v.split("."))))

    def fpm_bin(self, v: str) -> Path:
        return self.root / v / "php-cgi.exe"

    def cli_bin(self, v: str) -> Path:
        return self.root / v / "php.exe"

    def ext_dir(self, v: str) -> Path:
        return self.root / v / "ext"

    def server_argv(self, v: str) -> list[str]:
        return [*platform.self_command(), "_fcgi", "--php", str(self.fpm_bin(v)), "--port", str(base_port(v)),
                "--workers", str(WORKERS)]

    def upstream(self, v: str) -> str:
        return f"php{v.replace('.', '')}"

    def upstream_servers(self, v: str) -> list[str]:
        return [f"127.0.0.1:{base_port(v) + i}" for i in range(WORKERS)]

    def scan_env(self, v: str) -> dict[str, str]:
        return {"PHP_INI_SCAN_DIR": str(self.conf_d(v))}

    def install(self, v: str, fetch: Callable[[str], bytes]) -> Path:
        """Download the newest non-thread-safe x64 build of a version and check its SHA-256."""
        builds = json.loads(fetch(RELEASES).decode()).get(v)
        if not builds:
            raise PHPError(f"PHP {v} is not offered for Windows")
        key = next((k for k in builds if k.startswith("nts-") and k.endswith("-x64")), None)
        if key is None:
            raise PHPError(f"no 64-bit build of PHP {v}")
        z = builds[key]["zip"]
        data = fetch(DOWNLOADS + z["path"])
        if hashlib.sha256(data).hexdigest().lower() != z["sha256"].lower():
            raise PHPError(f"{z['path']}: checksum mismatch")
        unpack(data, self.root / v)
        return self.root / v

    def remove(self, v: str) -> None:
        import shutil
        shutil.rmtree(self.root / v, ignore_errors=True)

    # --- conf.d: extension_dir, the default extensions, then the settings ---
    def ensure_conf_d(self, v: str, settings: dict[str, str] | None = None) -> None:
        d = self.conf_d(v)
        d.mkdir(parents=True, exist_ok=True)
        base = d / "00-pipanel-base.ini"
        text = ("; Managed by PIPanel. Do not edit manually.\n"
                f'extension_dir = "{self.ext_dir(v).as_posix()}"\n'
                f'curl.cainfo = ""\n')
        if not base.exists() or base.read_text() != text:
            base.write_text(text)
        marker = d / ".seeded"
        if not marker.exists():
            available = {n for n, _ in self.extensions(v)}
            for name in DEFAULT_EXTENSIONS:
                if name in available:
                    self.set_extension(v, name, True)
            marker.write_text("")
        self.write_settings(v, settings or PHP_DEFAULTS)

    # --- extensions are php_<name>.dll files ---
    def _ext_ini(self, v: str, name: str) -> Path:
        return self.conf_d(v) / f"20-{name}.ini"

    def _enabled_link(self, v: str, name: str) -> Path | None:
        f = self._ext_ini(v, name)
        return f if f.exists() else None

    def extensions(self, v: str) -> list[tuple[str, bool]]:
        d = self.ext_dir(v)
        names = sorted(f.stem[4:] for f in d.glob("php_*.dll")) if d.is_dir() else []
        return [(n, self._enabled_link(v, n) is not None) for n in names]

    def set_extension(self, v: str, name: str, enabled: bool) -> None:
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise PHPError(f"invalid extension name: {name!r}")
        f = self._ext_ini(v, name)
        if enabled:
            if not (self.ext_dir(v) / f"php_{name}.dll").exists():
                raise PHPError(f"extension {name} is not installed for PHP {v}")
            key = "zend_extension" if name in ZEND else "extension"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"; Managed by PIPanel\n{key}={name}\n")
        else:
            f.unlink(missing_ok=True)

    def xdebug_state(self, v: str) -> str:
        if not (self.ext_dir(v) / "php_xdebug.dll").exists():
            return "missing"
        return "on" if self._enabled_link(v, "xdebug") else "off"

    def install_extension(self, v: str, name: str, fetch: Callable[[str], bytes]) -> None:
        """Xdebug from xdebug.org, other extensions (redis, apcu, ...) from PECL's Windows builds."""
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise PHPError(f"invalid extension name: {name!r}")
        target = self.ext_dir(v) / f"php_{name}.dll"
        if name == "xdebug":
            page = fetch(XDEBUG_PAGE).decode("utf-8", "replace")
            files = re.findall(rf"php_xdebug-[0-9.]+-{re.escape(v)}-nts-vs1[0-9]-x86_64\.dll", page)
            if not files:
                raise PHPError(f"no Xdebug build for PHP {v} on xdebug.org")
            target.write_bytes(fetch("https://xdebug.org/files/" + files[0]))
            return
        index = fetch(PECL + f"{name}/").decode("utf-8", "replace")
        versions = sorted(set(re.findall(r'href="(\d+\.\d+\.\d+)/"', index)), key=lambda s: tuple(map(int, s.split("."))))
        for ver in reversed(versions):
            listing = fetch(PECL + f"{name}/{ver}/").decode("utf-8", "replace")
            m = re.search(rf'php_{re.escape(name)}-{re.escape(ver)}-{re.escape(v)}-nts-vs1[0-9]-x64\.zip', listing)
            if m:
                target.write_bytes(member(fetch(PECL + f"{name}/{ver}/{m.group(0)}"), f"php_{name}.dll"))
                return
        raise PHPError(f"no Windows build of the {name} extension for PHP {v}")

    def install_composer(self, fetch: Callable[[str], bytes]) -> Path:
        """composer.phar (checksum verified) and composer.cmd."""
        phar = fetch(COMPOSER_URL)
        want = fetch(COMPOSER_URL + ".sha256sum").decode().split()[0].lower()
        if hashlib.sha256(phar).hexdigest() != want:
            raise PHPError("composer.phar checksum mismatch")
        self.paths.bin.mkdir(parents=True, exist_ok=True)
        (self.paths.bin / "composer.phar").write_bytes(phar)
        self.write_cli_wrapper(self.cli_version() or (self.installed() or ["8.4"])[-1])
        return self.paths.bin / "composer.cmd"

    # --- the `php` command: a .cmd that asks PIPanel which version the current folder uses ---
    def write_cli_wrapper(self, v: str) -> Path:
        self.paths.bin.mkdir(parents=True, exist_ok=True)
        launcher = " ".join(f'"{a}"' for a in platform.self_command())
        for name, tool in (("php.cmd", "php"), ("composer.cmd", "composer"), ("wp.cmd", "wp")):
            text = f"@echo off\r\nrem PIPanel: PHP CLI {v}\r\n{launcher} _run {tool} %*\r\n"
            f = self.paths.bin / name
            if not f.exists() or f.read_text() != text:
                f.write_text(text)
        return self.paths.bin / "php.cmd"

    def cli_version(self) -> str | None:
        try:
            m = re.search(r"PHP CLI (\d\.\d+)", (self.paths.bin / "php.cmd").read_text())
        except OSError:
            return None
        return m.group(1) if m else None
