"""Cloudflare quick tunnels: a public https://<random>.trycloudflare.com link without an account."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .ngrok import arch
from .paths import Paths
from .templates import valid_site_name

URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-{os}-{arch}{ext}"
PROGRAM_MAGIC = (b"\x7fELF", b"MZ", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")   # Linux, Windows, macOS
METRICS_PORT = 20251   # cloudflared's local metrics server; /quicktunnel reports the public host name


class CloudflareError(Exception):
    pass


def parse_quicktunnel(payload: str) -> str | None:
    try:
        host = json.loads(payload).get("hostname")
    except (ValueError, AttributeError):
        return None
    return f"https://{host}" if isinstance(host, str) and host else None


class Cloudflared:
    def __init__(self, paths: Paths):
        self.paths = paths

    @property
    def bin(self) -> Path:
        from . import platform
        return self.paths.bin / platform.exe("cloudflared")

    def installed(self) -> bool:
        return self.bin.exists()

    def install(self, fetch: Callable[[str], bytes], machine: str | None = None) -> Path:
        from . import platform
        if platform.is_windows():
            data = fetch(URL.format(os="windows", arch=arch(machine), ext=".exe"))
        elif platform.is_macos():   # a .tgz with the program inside
            from .archive import member
            data = member(fetch(URL.format(os="darwin", arch=arch(machine), ext=".tgz")), "cloudflared")
        else:
            data = fetch(URL.format(os="linux", arch=arch(machine), ext=""))
        if not data.startswith(PROGRAM_MAGIC):
            raise CloudflareError("the download is not a program for this computer")
        self.paths.bin.mkdir(parents=True, exist_ok=True)
        self.bin.write_bytes(data)
        self.bin.chmod(0o755)
        return self.bin

    def unit_spec(self, site: str, tld: str = "test", port: int = 80) -> dict:
        if not valid_site_name(site):
            raise CloudflareError(f"invalid site name: {site!r}")
        return dict(description=f"PIPanel: Cloudflare tunnel {site}",
                    argv=[str(self.bin), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}",
                          "--http-host-header", f"{site}.{tld}", "--metrics", f"127.0.0.1:{METRICS_PORT}"])

    def public_url(self, fetch: Callable[[str], bytes]) -> str | None:
        try:
            return parse_quicktunnel(fetch(f"http://127.0.0.1:{METRICS_PORT}/quicktunnel").decode())
        except OSError:
            return None
