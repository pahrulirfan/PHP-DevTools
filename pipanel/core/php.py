"""User-owned PHP: per-version conf.d, extensions, CLI wrapper, Composer. Never touches /etc/php."""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Callable

from .config import PHP_DEFAULTS
from .paths import Paths

SUPPORTED = ["8.5", "8.4", "8.3", "8.2", "8.1"]
INI_NAME = "99-pipanel.ini"
# Needed by Laravel and Composer; disabling any of these usually breaks the app.
ESSENTIAL = frozenset({"ctype", "curl", "dom", "fileinfo", "filter", "iconv", "mbstring", "openssl", "pdo", "phar",
                       "simplexml", "tokenizer", "xml", "xmlwriter"})
# Common extras that are not part of the base packages (installed with apt: php8.x-<name>).
COMMON_EXTRAS = ["redis", "imagick", "xdebug", "memcached", "mongodb", "pgsql", "soap", "imap", "ldap", "apcu",
                 "yaml", "gmp", "bz2", "pcov", "swoole", "snmp", "tidy"]
COMPOSER_URL = "https://getcomposer.org/download/latest-stable/composer.phar"

_SETTING_RE = {
    "memory_limit": re.compile(r"^(-1|\d+[KMG]?)$"),
    "upload_max_filesize": re.compile(r"^\d+[KMG]?$"),
    "post_max_size": re.compile(r"^\d+[KMG]?$"),
    "max_execution_time": re.compile(r"^\d+$"),
}
_EXT_RE = re.compile(r"^[a-z0-9_]+$")
_PRIORITY_RE = re.compile(r"^;\s*priority\s*=\s*(\d+)", re.M)


class PHPError(Exception):
    pass


def validate_setting(key: str, value: str) -> str:
    rx = _SETTING_RE.get(key)
    value = value.strip()
    if rx is None or not rx.match(value):
        raise PHPError(f"invalid value for {key}: {value!r}")
    return value


class PHP:
    def __init__(self, paths: Paths, *, etc_php: Path = Path("/etc/php"),
                 sbin: Path = Path("/usr/sbin"), usr_bin: Path = Path("/usr/bin")):
        self.paths, self.etc_php, self.sbin, self.usr_bin = paths, etc_php, sbin, usr_bin

    # --- versions ---
    def installed(self) -> list[str]:
        vs = [v for v in SUPPORTED if (self.sbin / f"php-fpm{v}").exists()]
        return sorted(vs, key=lambda v: tuple(map(int, v.split("."))))

    def fpm_bin(self, v: str) -> Path:
        return self.sbin / f"php-fpm{v}"

    def cli_bin(self, v: str) -> Path:
        return self.usr_bin / f"php{v}"

    def conf_d(self, v: str) -> Path:
        return self.paths.php_dir(v) / "conf.d"

    def cli_cmd(self, v: str, args: list[str]) -> tuple[list[str], dict[str, str]]:
        """argv + environment to run `php<v> <args>` with PIPanel's own conf.d (extensions and ini settings)."""
        return [str(self.cli_bin(v)), *args], self.scan_env(v)

    def scan_env(self, v: str) -> dict[str, str]:
        return {"PHP_INI_SCAN_DIR": str(self.conf_d(v))}

    # --- conf.d ---
    def ensure_conf_d(self, v: str, settings: dict[str, str] | None = None) -> None:
        d = self.conf_d(v)
        d.mkdir(parents=True, exist_ok=True)
        marker = d / ".seeded"
        if not marker.exists():
            src = self.etc_php / v / "fpm" / "conf.d"
            if src.is_dir():
                for link in sorted(src.glob("*.ini")):
                    dst = d / link.name
                    if not dst.exists() and not dst.is_symlink():
                        dst.symlink_to(link.resolve())
            marker.write_text("")
        self.write_settings(v, settings or PHP_DEFAULTS)

    def write_settings(self, v: str, settings: dict[str, str]) -> bool:
        lines = ["; Managed by PIPanel. Do not edit manually."]
        for k in PHP_DEFAULTS:
            lines.append(f"{k} = {validate_setting(k, settings.get(k, PHP_DEFAULTS[k]))}")
        text = "\n".join(lines) + "\n"
        f = self.conf_d(v) / INI_NAME
        if f.exists() and f.read_text() == text:
            return False
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return True

    # --- extensions ---
    def _mods(self, v: str) -> Path:
        return self.etc_php / v / "mods-available"

    def _priority(self, v: str, name: str) -> int:
        try:
            m = _PRIORITY_RE.search((self._mods(v) / f"{name}.ini").read_text())
            return int(m.group(1)) if m else 20
        except OSError:
            return 20

    def _enabled_link(self, v: str, name: str) -> Path | None:
        for f in self.conf_d(v).glob("*.ini"):
            if re.fullmatch(rf"\d+-{re.escape(name)}\.ini", f.name):
                return f
        return None

    def extensions(self, v: str) -> list[tuple[str, bool]]:
        mods = self._mods(v)
        names = sorted(p.stem for p in mods.glob("*.ini")) if mods.is_dir() else []
        return [(n, self._enabled_link(v, n) is not None) for n in names]

    def set_extension(self, v: str, name: str, enabled: bool) -> None:
        if not _EXT_RE.match(name):
            raise PHPError(f"invalid extension name: {name!r}")
        existing = self._enabled_link(v, name)
        if enabled and existing is None:
            src = self._mods(v) / f"{name}.ini"
            if not src.exists():
                raise PHPError(f"extension {name} is not installed for PHP {v}")
            (self.conf_d(v)).mkdir(parents=True, exist_ok=True)
            (self.conf_d(v) / f"{self._priority(v, name)}-{name}.ini").symlink_to(src.resolve())
        elif not enabled and existing is not None:
            existing.unlink()

    # --- CLI wrapper ---
    def write_cli_wrapper(self, v: str) -> Path:
        self.paths.bin.mkdir(parents=True, exist_ok=True)
        f = self.paths.bin / "php"
        f.write_text(
            "#!/bin/sh\n"
            f"# PIPanel: PHP CLI {v}\n"
            f'export PHP_INI_SCAN_DIR="{self.conf_d(v)}"\n'
            f'exec "{self.cli_bin(v)}" "$@"\n'
        )
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return f

    def cli_version(self) -> str | None:
        try:
            m = re.search(r"PHP CLI (\d\.\d+)", (self.paths.bin / "php").read_text())
        except OSError:
            return None
        return m.group(1) if m else None

    # --- Composer ---
    def install_composer(self, fetch: Callable[[str], bytes]) -> Path:
        """Download composer.phar and verify its SHA-256 against the official site."""
        phar = fetch(COMPOSER_URL)
        want = fetch(COMPOSER_URL + ".sha256sum").decode().split()[0].lower()
        got = hashlib.sha256(phar).hexdigest()
        if got != want:
            raise PHPError("composer.phar checksum mismatch")
        self.paths.bin.mkdir(parents=True, exist_ok=True)
        (self.paths.bin / "composer.phar").write_bytes(phar)
        w = self.paths.bin / "composer"
        w.write_text('#!/bin/sh\nexec "$(dirname "$0")/php" "$(dirname "$0")/composer.phar" "$@"\n')
        w.chmod(0o755)
        return w


def http_fetch(url: str) -> bytes:
    import urllib.request
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 (https only)
        return r.read()
