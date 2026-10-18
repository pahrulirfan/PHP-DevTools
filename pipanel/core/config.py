"""Read/write config.json. Invalid values fall back to defaults."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

THEMES = ("system", "light", "dark")
DB_ENGINES = ("mysql", "mariadb")
SHARE_PROVIDERS = ("ngrok", "cloudflare")
RECENT = 8   # sites remembered for the tray menu
TOOLS = ("mysql", "mail", "redis")   # optional servers whose on/off choice is remembered (services.ADDONS)
PHP_DEFAULTS = {
    "memory_limit": "512M",
    "upload_max_filesize": "128M",
    "post_max_size": "128M",
    "max_execution_time": "60",
}


@dataclass
class SiteOverride:
    php: str | None = None
    https: bool = False
    docroot: str | None = None   # web root relative to the project folder, e.g. "public" or "."


@dataclass
class Config:
    theme: str = "system"
    tld: str = "test"
    default_php: str = "8.3"
    parked: list[str] = field(default_factory=list)          # folders that are scanned
    linked: dict[str, str] = field(default_factory=dict)     # name -> path
    proxies: dict[str, str] = field(default_factory=dict)    # name -> http://127.0.0.1:3000 (another dev server)
    sites: dict[str, SiteOverride] = field(default_factory=dict)  # name -> override
    dns_port: int = 5354
    mysql_port: int = 3306
    db_engine: str = "mysql"   # "mysql" or "mariadb"; they share the port, so only one runs at a time
    http_port: int = 80
    ngrok_verified: str = ""   # fingerprint of the ngrok token that was verified (never the token itself)
    share_provider: str = "ngrok"   # "ngrok" (needs an account) or "cloudflare" (quick tunnel, no account)
    https_port: int = 443
    autostart_services: bool = True   # enable the systemd user units so they start at login
    notify: bool = True   # a desktop notification when a service stops working
    recent_sites: list[str] = field(default_factory=list)   # most recently opened first (tray menu)
    stopped_tools: list[str] = field(default_factory=list)   # database/Mailpit/Redis the user turned off: they
                                                              # stay off with "Start all" and at login
    php_settings: dict[str, str] = field(default_factory=lambda: dict(PHP_DEFAULTS))

    def site(self, name: str) -> SiteOverride:
        return self.sites.get(name) or SiteOverride()

    def php_for(self, name: str) -> str:
        return self.site(name).php or self.default_php

    # --- serialisasi ---
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        d = cls()
        if isinstance(data.get("ngrok_verified"), str):
            d.ngrok_verified = data["ngrok_verified"][:64]
        if data.get("share_provider") in SHARE_PROVIDERS:
            d.share_provider = data["share_provider"]
        if data.get("db_engine") in DB_ENGINES:
            d.db_engine = data["db_engine"]
        if data.get("theme") in THEMES:
            d.theme = data["theme"]
        for key in ("tld", "default_php"):
            if isinstance(data.get(key), str) and data[key]:
                setattr(d, key, data[key])
        for key in ("dns_port", "mysql_port", "http_port", "https_port"):
            if isinstance(data.get(key), int) and 0 < data[key] < 65536:
                setattr(d, key, data[key])
        if isinstance(data.get("notify"), bool):
            d.notify = data["notify"]
        if isinstance(data.get("recent_sites"), list):
            d.recent_sites = [s for s in data["recent_sites"] if isinstance(s, str)][:RECENT]
        if isinstance(data.get("stopped_tools"), list):
            d.stopped_tools = [s for s in data["stopped_tools"] if s in TOOLS]
        if isinstance(data.get("autostart_services"), bool):
            d.autostart_services = data["autostart_services"]
        if isinstance(data.get("php_settings"), dict):
            d.php_settings.update({k: v for k, v in data["php_settings"].items()
                                   if k in PHP_DEFAULTS and isinstance(v, str)})
        if isinstance(data.get("parked"), list):
            d.parked = [p for p in data["parked"] if isinstance(p, str)]
        if isinstance(data.get("linked"), dict):
            d.linked = {k: v for k, v in data["linked"].items() if isinstance(v, str)}
        if isinstance(data.get("proxies"), dict):
            d.proxies = {k: v for k, v in data["proxies"].items() if isinstance(v, str)}
        if isinstance(data.get("sites"), dict):
            for name, o in data["sites"].items():
                if isinstance(o, dict):
                    d.sites[name] = SiteOverride(
                        php=o.get("php") if isinstance(o.get("php"), str) else None,
                        https=bool(o.get("https", False)),
                        docroot=o.get("docroot") if isinstance(o.get("docroot"), str) else None,
                    )
        return d


def defaults() -> Config:
    """A new configuration for this operating system."""
    from . import platform
    cfg = Config()
    if platform.is_windows():
        cfg.dns_port = 53   # the Windows DNS client can only send to port 53
        cfg.default_php = "8.4"
    elif platform.is_macos():
        cfg.default_php = "8.4"
    return cfg


def load(path: Path) -> Config:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return defaults()
    if not isinstance(data, dict):
        return defaults()
    cfg = Config.from_dict(data)
    if "dns_port" not in data:
        cfg.dns_port = defaults().dns_port
    return cfg


def save(cfg: Config, path: Path) -> None:
    """Write atomically (temp file, then rename) so the file is never half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
