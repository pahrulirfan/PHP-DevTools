"""Scan parked/linked folders; detect the project type and docroot."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .templates import valid_site_name

LARAVEL, WORDPRESS, PUBLIC, PLAIN = "laravel", "wordpress", "public", "php"


@dataclass(frozen=True)
class Site:
    name: str
    path: Path
    docroot: Path
    kind: str
    php: str
    https: bool
    linked: bool = False
    custom_docroot: bool = False   # web root chosen by the user instead of auto-detected

    @property
    def host(self) -> str:  # without the TLD; the caller builds the full host
        return self.name


@dataclass(frozen=True)
class Proxy:
    """A *.test name that Nginx forwards to another local server (Vite, Next.js, a Go or Python app, Mailpit)."""
    name: str
    target: str           # normalized, e.g. http://127.0.0.1:3000
    https: bool = False
    local_only: bool = False   # answer only requests from this computer (tools such as Mailpit)
    internal: bool = False     # managed by PIPanel itself, not listed on the Sites page


_TARGET_RE = re.compile(r"^(https?)://([A-Za-z0-9.-]+|\[[0-9a-fA-F:]+\])(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?$")


def normalize_target(target: str) -> str | None:
    """'3000' -> http://127.0.0.1:3000, 'localhost:5173' -> http://localhost:5173; None when it is not a plain
    http(s) URL (the result goes into an nginx config, so nothing but host, port and path is accepted)."""
    t = target.strip().rstrip("/")
    if re.fullmatch(r"\d{1,5}", t):
        t = f"http://127.0.0.1:{t}"
    elif not re.match(r"^https?://", t):
        t = f"http://{t}"
    m = _TARGET_RE.match(t)
    if not m or (m.group(3) and not 0 < int(m.group(3)[1:]) < 65536):
        return None
    return t


def detect(path: Path) -> tuple[str, Path]:
    """Return (kind, docroot)."""
    if (path / "artisan").is_file() and (path / "public").is_dir():
        return LARAVEL, path / "public"
    if (path / "wp-config.php").is_file() or (path / "wp-content").is_dir():
        return WORDPRESS, path
    if (path / "public" / "index.php").is_file():
        return PUBLIC, path / "public"
    return PLAIN, path


def safe_subdir(base: Path, rel: str) -> Path | None:
    """`base/rel` as a resolved directory that stays inside `base`, else None."""
    if Path(rel).is_absolute():
        return None
    root = base.resolve()
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        return None
    return p if p.is_dir() else None


def relative_docroot(site: "Site") -> str:
    try:
        rel = site.docroot.resolve().relative_to(site.path.resolve())
    except ValueError:
        return str(site.docroot)
    return str(rel)   # "." for the project folder itself


def slugify(name: str) -> str | None:
    """Folder name -> valid host name, or None if it cannot be used."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s if valid_site_name(s) else None


def scan(cfg: Config) -> list[Site]:
    found: dict[str, Site] = {}

    def add(name: str, path: Path, linked: bool) -> None:
        kind, docroot = detect(path)
        custom = False
        sub = cfg.site(name).docroot
        if sub:
            chosen = safe_subdir(path, sub)
            if chosen is not None:
                docroot, custom = chosen, True
        found[name] = Site(name, path, docroot, kind, cfg.php_for(name), cfg.site(name).https, linked, custom)

    for parked in cfg.parked:
        root = Path(parked).expanduser()
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            name = slugify(child.name)
            if name and name not in found:  # name clash: the first one wins
                add(name, child, False)

    for name, path in cfg.linked.items():  # an explicit link overrides parking
        p = Path(path).expanduser()
        if valid_site_name(name) and p.is_dir():
            add(name, p, True)

    return sorted(found.values(), key=lambda s: s.name)
