"""Where the programs PIPanel runs come from, per operating system.

- Linux: distribution packages through the root helper (apt; see packaging/pipanel-helper).
- macOS: Homebrew (nginx, PHP from the shivammathur/php tap, MariaDB/MySQL, Redis), installed as the user.
- Windows: the official zip builds (nginx.org, windows.php.net, mariadb.org, mysql.com), unpacked into
  %LOCALAPPDATA%\\PIPanel; nothing needs administrator rights except the one-time DNS rule.

Small single-file tools (mkcert, Mailpit, cloudflared, ngrok, fnm) are downloaded on every system.
"""
from __future__ import annotations

import hashlib
import json
import platform as _pyplatform
import re
import shutil
from pathlib import Path
from typing import Callable

from . import platform
from .archive import unpack
from .paths import Paths
from .runner import Result, Runner

Fetch = Callable[[str], bytes]
NGINX_DOWNLOAD = "https://nginx.org/en/download.html"
MARIADB_API = "https://downloads.mariadb.org/rest-api/mariadb/"
MARIADB_SERIES = "11.4"                    # long-term support
MYSQL_SERIES = "8.4"                       # long-term support
MYSQL_PAGE = "https://dev.mysql.com/downloads/mysql/8.4.html"
MYSQL_CDN = "https://cdn.mysql.com/Downloads/MySQL-8.4/"
REDIS_WINDOWS = "https://api.github.com/repos/redis-windows/redis-windows/releases/latest"
MKCERT = "https://github.com/FiloSottile/mkcert/releases/download/v1.4.4/mkcert-v1.4.4-{os}-{arch}{ext}"


class StackError(Exception):
    pass


def machine() -> str:
    m = _pyplatform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "amd64"


class Stack:
    """Linux: everything comes from apt through the root helper (the controller calls it)."""
    name = platform.LINUX
    dns_port = 5354

    def __init__(self, paths: Paths, runner: Runner):
        self.paths, self.runner = paths, runner

    def make_php(self):
        from .php import PHP
        return PHP(self.paths)

    nginx_bin: str | None = None           # None: look for it in the usual system folders
    etc_nginx: str = "/etc/nginx"
    redis_server = "/usr/bin/redis-server"
    mkcert = "mkcert"

    def dns_argv(self, port: int, tld: str) -> list[str] | None:
        return None                         # dnsmasq

    def db(self, engine: str) -> dict:
        return {}                           # the paths in mysql.ENGINES

    def ready(self) -> bool:
        return True


class MacStack(Stack):
    name = platform.MACOS

    def __init__(self, paths: Paths, runner: Runner, prefix: Path | None = None):
        super().__init__(paths, runner)
        from .php_mac import brew_prefix
        self.prefix = prefix or brew_prefix()

    def make_php(self):
        from .php_mac import BrewPHP
        return BrewPHP(self.paths, self.prefix)

    @property
    def nginx_bin(self) -> str:
        return str(self.prefix / "bin" / "nginx")

    @property
    def etc_nginx(self) -> str:
        return str(self.prefix / "etc" / "nginx")

    @property
    def redis_server(self) -> str:
        return str(self.prefix / "opt" / "redis" / "bin" / "redis-server")

    @property
    def mkcert(self) -> str:
        return str(self.paths.bin / "mkcert")

    def dns_argv(self, port: int, tld: str) -> list[str]:
        return [*platform.self_command(), "_dns", "--port", str(port), "--tld", tld]

    def db(self, engine: str) -> dict:
        if engine == "mariadb":
            b = self.prefix / "opt" / "mariadb" / "bin"
            return dict(mysqld=str(b / "mariadbd"), client=str(b / "mariadb"), init=str(b / "mariadb-install-db"),
                        dump=str(b / "mariadb-dump"), basedir=str(self.prefix / "opt" / "mariadb"))
        b = self.prefix / "opt" / f"mysql@{MYSQL_SERIES}" / "bin"
        return dict(mysqld=str(b / "mysqld"), client=str(b / "mysql"), dump=str(b / "mysqldump"),
                    basedir=str(self.prefix / "opt" / f"mysql@{MYSQL_SERIES}"))

    def has_brew(self) -> bool:
        return (self.prefix / "bin" / "brew").exists()

    def brew(self, *formulas: str) -> Result:
        if not self.has_brew():
            return Result(127, "", "Homebrew is needed: install it from https://brew.sh, then try again.")
        return self.runner.run(["brew", "install", *formulas], timeout=3600,
                               env={"HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_INSTALL_CLEANUP": "1"})

    def install_base(self, fetch: Fetch) -> Result:
        r = self.brew("nginx")
        if r.ok:
            install_mkcert(self.paths, fetch)
        return r

    def install_database(self, engine: str, fetch: Fetch) -> Result:
        return self.brew("mariadb" if engine == "mariadb" else f"mysql@{MYSQL_SERIES}")

    def install_redis(self, fetch: Fetch) -> Result:
        return self.brew("redis")

    def ready(self) -> bool:
        return Path(self.nginx_bin).exists()


class WindowsStack(Stack):
    name = platform.WINDOWS
    dns_port = 53   # the Windows DNS client (NRPT) can only send to port 53

    def __init__(self, paths: Paths, runner: Runner, root: Path | None = None):
        super().__init__(paths, runner)
        self.root = root or paths.data / "stack"

    def make_php(self):
        from .php_win import WindowsPHP
        return WindowsPHP(self.paths, self.root / "php")

    @property
    def nginx_bin(self) -> str:
        return str(self.root / "nginx" / "nginx.exe")

    @property
    def etc_nginx(self) -> str:
        return (self.root / "nginx" / "conf").as_posix()

    @property
    def redis_server(self) -> str:
        return str(self.root / "redis" / "redis-server.exe")

    @property
    def mkcert(self) -> str:
        return str(self.paths.bin / "mkcert.exe")

    def dns_argv(self, port: int, tld: str) -> list[str]:
        return [*platform.self_command(), "_dns", "--port", str(port), "--tld", tld]

    def db(self, engine: str) -> dict:
        b = self.root / engine / "bin"
        if engine == "mariadb":
            return dict(mysqld=str(b / "mariadbd.exe"), client=str(b / "mariadb.exe"),
                        init=str(b / "mariadb-install-db.exe"), dump=str(b / "mariadb-dump.exe"),
                        basedir=str(self.root / engine))
        return dict(mysqld=str(b / "mysqld.exe"), client=str(b / "mysql.exe"), dump=str(b / "mysqldump.exe"),
                    basedir=str(self.root / engine))

    # ---- downloads ----
    def install_nginx(self, fetch: Fetch) -> Path:
        page = fetch(NGINX_DOWNLOAD).decode("utf-8", "replace")
        stable = page.split("Stable version", 1)[-1]
        m = re.search(r"/download/(nginx-\d+\.\d+\.\d+)\.zip", stable)
        if not m:
            raise StackError("could not find the stable nginx version on nginx.org")
        return unpack(fetch(f"https://nginx.org/download/{m.group(1)}.zip"), self.root / "nginx", strip=1)

    def install_base(self, fetch: Fetch) -> Result:
        try:
            self.install_nginx(fetch)
            install_mkcert(self.paths, fetch)
        except Exception as e:  # noqa: BLE001 - shown as a failed step
            return Result(1, "", str(e))
        return Result(0)

    def install_database(self, engine: str, fetch: Fetch) -> Result:
        try:
            if engine == "mariadb":
                api = json.loads(fetch(f"{MARIADB_API}{MARIADB_SERIES}/latest/").decode())
                files = next(iter(api["releases"].values()))["files"]
                f = next(f for f in files if f["file_name"].endswith("-winx64.zip"))
                data = fetch(f["file_download_url"].replace("http://", "https://"))
                want = (f.get("checksum") or {}).get("sha256sum")
                if want and hashlib.sha256(data).hexdigest() != want:
                    raise StackError(f"{f['file_name']}: checksum mismatch")
            else:
                page = fetch(MYSQL_PAGE).decode("utf-8", "replace")
                m = re.search(rf"mysql-{re.escape(MYSQL_SERIES)}\.\d+-winx64\.zip", page)
                if not m:
                    raise StackError("could not find the MySQL download")
                data = fetch(MYSQL_CDN + m.group(0))
            unpack(data, self.root / engine, strip=1)
        except Exception as e:  # noqa: BLE001
            return Result(1, "", str(e))
        return Result(0)

    def install_redis(self, fetch: Fetch) -> Result:
        try:
            rel = json.loads(fetch(REDIS_WINDOWS).decode())
            asset = next(a for a in rel["assets"] if re.fullmatch(r"Redis-[\d.]+-Windows-x64-msys2\.zip", a["name"]))
            unpack(fetch(asset["browser_download_url"]), self.root / "redis", strip=1)
            if not (self.root / "redis" / "redis-server.exe").exists():   # some releases have no top folder
                found = next((self.root / "redis").rglob("redis-server.exe"), None)
                if found is None:
                    raise StackError("redis-server.exe is not in the download")
                for f in found.parent.iterdir():
                    shutil.move(str(f), str(self.root / "redis" / f.name))
        except Exception as e:  # noqa: BLE001
            return Result(1, "", str(e))
        return Result(0)

    def ready(self) -> bool:
        return Path(self.nginx_bin).exists()


def install_mkcert(paths: Paths, fetch: Fetch) -> Path:
    os_name = {platform.WINDOWS: "windows", platform.MACOS: "darwin"}.get(platform.current(), "linux")
    ext = ".exe" if platform.is_windows() else ""
    data = fetch(MKCERT.format(os=os_name, arch=machine(), ext=ext))
    paths.bin.mkdir(parents=True, exist_ok=True)
    f = paths.bin / f"mkcert{ext}"
    f.write_bytes(data)
    f.chmod(0o755)
    return f


def for_platform(paths: Paths, runner: Runner) -> Stack:
    return {platform.WINDOWS: WindowsStack, platform.MACOS: MacStack}.get(platform.current(), Stack)(paths, runner)
