"""User-owned PHP: per-version conf.d, extensions, CLI wrapper, Composer. Never touches /etc/php."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
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

    reload_how = "signal:USR2"   # PHP-FPM reloads its workers on SIGUSR2
    uses_fpm_conf = True         # PIPanel writes a php-fpm.conf per version

    def cli_template(self) -> str:
        """Path of the CLI binary of version $v, for the shell wrapper."""
        return f"{self.usr_bin}/php$v"

    def upstream_servers(self, v: str) -> list[str]:
        return []   # PHP-FPM listens on a unix socket; no upstream block

    def version_for(self, folder: Path, default: str) -> str:
        """The PHP version of the site that contains `folder` (the deepest one), else `default`."""
        try:
            rows = [l.split("\t") for l in self.site_map.read_text().splitlines() if "\t" in l]
        except OSError:
            return default
        here = [str(folder).rstrip("/\\") + "/", str(Path(folder).resolve()).rstrip("/\\") + "/"]
        for site, v in rows:   # deepest first
            prefix = site.rstrip("/\\") + "/"
            if any(h.replace("\\", "/").lower().startswith(prefix.replace("\\", "/").lower())
                   if sys.platform.startswith("win") else h.startswith(prefix) for h in here):
                return v if v in self.installed() else default
        return default

    def server_argv(self, v: str) -> list[str]:
        """The PHP server that Nginx talks to (PHP-FPM here)."""
        return [str(self.fpm_bin(v)), "--nodaemonize", "--fpm-config", str(self.paths.php_dir(v) / "php-fpm.conf")]

    def upstream(self, v: str) -> str:
        """What nginx's fastcgi_pass points at."""
        return f"unix:{self.paths.php_socket(v)}"

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

    # --- Xdebug ---
    XDEBUG_INI = "99-pipanel-xdebug.ini"

    def xdebug_state(self, v: str) -> str:
        """'missing' (package not installed), 'off', or 'on'."""
        if "xdebug" not in {n for n, _ in self.extensions(v)}:
            return "missing"
        return "on" if self._enabled_link(v, "xdebug") else "off"

    def set_xdebug(self, v: str, on: bool) -> None:
        """Step debugging for an IDE listening on port 9003. Off by default: Xdebug slows every request down."""
        self.set_extension(v, "xdebug", on)
        f = self.conf_d(v) / self.XDEBUG_INI
        if on:
            f.write_text("; Managed by PIPanel. Do not edit manually.\n"
                         "xdebug.mode = debug,develop\n"
                         "xdebug.start_with_request = yes\n"
                         "xdebug.client_host = 127.0.0.1\n"
                         "xdebug.client_port = 9003\n"
                         "; no warning on every request when no editor is listening\n"
                         "xdebug.log_level = 0\n")
        else:
            f.unlink(missing_ok=True)

    # --- CLI wrapper ---
    @property
    def site_map(self) -> Path:
        return self.paths.config / "php" / "sites.map"

    def write_site_map(self, sites: list[tuple[Path, str]]) -> bool:
        """Folder -> PHP version of every site, deepest folder first, read by the `php` wrapper."""
        rows: dict[str, str] = {}
        for path, v in sites:
            for p in dict.fromkeys((str(path), str(Path(path).resolve()))):
                if "\t" not in p and "\n" not in p and re.fullmatch(r"8\.\d", v):
                    rows[p.rstrip("/") or "/"] = v
        text = "".join(f"{p}\t{v}\n" for p, v in sorted(rows.items(), key=lambda kv: (-len(kv[0]), kv[0])))
        f = self.site_map
        if f.exists() and f.read_text() == text:
            return False
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return True

    def write_cli_wrapper(self, v: str) -> Path:
        """`php` for terminals: inside a site folder it runs that site's PHP version (like Herd), elsewhere the
        default `v`. PIPANEL_PHP=8.x picks a version explicitly."""
        self.paths.bin.mkdir(parents=True, exist_ok=True)
        f = self.paths.bin / "php"
        conf_root = self.paths.config / "php"
        text = (
            "#!/bin/sh\n"
            f"# PIPanel: PHP CLI {v}\n"
            f'v="{v}"\n'
            'if [ -n "$PIPANEL_PHP" ]; then\n'
            '  v="$PIPANEL_PHP"\n'
            f'elif [ -r "{self.site_map}" ]; then\n'
            '  for d in "$PWD/" "$(pwd -P)/"; do\n'
            "    while IFS='\t' read -r p pv; do\n"
            '      case "$d" in "$p"/*) v="$pv"; break 2 ;; esac\n'
            f'    done < "{self.site_map}"\n'
            "  done\n"
            "fi\n"
            f'case "$v" in 8.[0-9]) ;; *) v="{v}" ;; esac\n'
            f'[ -x "{self.cli_template()}" ] || v="{v}"\n'
            f'export PHP_INI_SCAN_DIR="{conf_root}/$v/conf.d"\n'
            f'exec "{self.cli_template()}" "$@"\n'
        )
        if not f.exists() or f.read_text() != text:
            f.write_text(text)
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


def http_fetch(url: str, attempts: int = 6) -> bytes:
    """Download a file. A connection that drops halfway (common for the large cloudflared and Mailpit
    downloads on a slow line) is resumed with a Range request instead of starting over."""
    import http.client
    import time
    import urllib.error
    import urllib.request
    data, total = bytearray(), None
    for i in range(attempts):
        req = urllib.request.Request(url, headers={"Range": f"bytes={len(data)}-"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 (https only)
                if data and r.status != 206:   # the server ignored the range: start over
                    data.clear()
                if total is None and r.headers.get("Content-Length") and r.status == 200:
                    total = int(r.headers["Content-Length"])
                while chunk := r.read(1 << 20):
                    data += chunk
        except urllib.error.HTTPError:
            raise   # the server answered: retrying will not help
        except (http.client.IncompleteRead, OSError) as e:
            if i == attempts - 1:
                raise
            if isinstance(e, http.client.IncompleteRead):
                data += e.partial
            time.sleep(1 + i)
            continue
        if total is None or len(data) >= total:
            return bytes(data)
    raise OSError(f"download incomplete: {url} ({len(data)} of {total} bytes)")
