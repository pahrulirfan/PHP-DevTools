"""Cloudflare quick tunnels: a public https://<random>.trycloudflare.com link without an account."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .ngrok import arch
from .paths import Paths
from .templates import valid_site_name

URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
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
        return self.paths.bin / "cloudflared"

    def installed(self) -> bool:
        return self.bin.exists()

    def install(self, fetch: Callable[[str], bytes], machine: str | None = None) -> Path:
        data = fetch(URL.format(arch=arch(machine)))
        if not data.startswith(b"\x7fELF"):
            raise CloudflareError("the download is not a Linux program")
        self.paths.bin.mkdir(parents=True, exist_ok=True)
        self.bin.write_bytes(data)
        self.bin.chmod(0o755)
        return self.bin

    def unit_spec(self, site: str, tld: str = "test", port: int = 80) -> dict:
        if not valid_site_name(site):
            raise CloudflareError(f"invalid site name: {site!r}")
        return dict(description=f"PIPanel: Cloudflare tunnel {site}",
                    exec_start=f"{self.bin} tunnel --no-autoupdate --url http://127.0.0.1:{port} "
                               f"--http-host-header {site}.{tld} --metrics 127.0.0.1:{METRICS_PORT}")

    def public_url(self, fetch: Callable[[str], bytes]) -> str | None:
        try:
            return parse_quicktunnel(fetch(f"http://127.0.0.1:{METRICS_PORT}/quicktunnel").decode())
        except OSError:
            return None
