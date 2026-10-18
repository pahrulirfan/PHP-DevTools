"""Log sources and file tailing."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .paths import Paths
from .runner import Runner
from .services import service_title, unit_name

LEVELS = ("debug", "info", "notice", "warning", "error", "critical", "alert", "emergency")
_ENTRY = re.compile(r"^\[\d{4}-\d{2}-\d{2}[ T][^\]]*\] [\w-]+\.([A-Z]+):")


@dataclass(frozen=True)
class LogSource:
    label: str
    path: Path | None = None
    unit: str | None = None   # service name (without pipanel-), read via journalctl
    laravel: bool = False     # a Laravel log: entries have a level that can be filtered


def laravel_log(project: Path) -> Path:
    """storage/logs/laravel.log, or the newest daily log (laravel-2026-10-18.log) when the project uses those."""
    d = project / "storage" / "logs"
    single = d / "laravel.log"
    daily = sorted(d.glob("laravel-*.log")) if d.is_dir() else []
    if daily and (not single.exists() or daily[-1].stat().st_mtime > single.stat().st_mtime):
        return daily[-1]
    return single


def sources(paths: Paths, site_names: list[str], php_versions: list[str], *,
            laravel: dict[str, Path] | None = None, units: list[str] = ()) -> list[LogSource]:
    """`laravel`: site name -> project folder of the Laravel sites; `units`: other services with a journal
    (Mailpit, Redis, tunnels, site processes)."""
    laravel = laravel or {}
    out = [LogSource("Nginx (error)", unit="nginx"), LogSource("DNS (dnsmasq)", unit="dns")]
    out += [LogSource(f"PHP-FPM {v}", path=paths.logs / f"php{v}-fpm.log") for v in php_versions]
    out += [LogSource("Database server", path=paths.logs / "mysql.log"), LogSource("ngrok", unit="ngrok")]
    out += [LogSource(service_title(u), unit=u) for u in units if u != "ngrok"]
    for n in site_names:
        if n in laravel:
            out.append(LogSource(f"{n} (laravel.log)", path=laravel_log(laravel[n]), laravel=True))
        out.append(LogSource(f"{n} (access)", path=paths.logs / f"{n}-access.log"))
        out.append(LogSource(f"{n} (error)", path=paths.logs / f"{n}-error.log"))
    return out


def filter_level(text: str, minimum: str) -> str:
    """Keep the Laravel log entries at `minimum` level or above. An entry is its header line plus the lines
    that follow it (stack traces)."""
    if minimum not in LEVELS or minimum == "debug":
        return text
    floor = LEVELS.index(minimum)
    out, keep = [], False
    for line in text.splitlines():
        m = _ENTRY.match(line)
        if m:
            level = m.group(1).lower()
            keep = level in LEVELS and LEVELS.index(level) >= floor
        if keep:
            out.append(line)
    return "\n".join(out)


def clear(path: Path) -> None:
    """Empty a log file (it is kept, so a running writer can continue)."""
    with open(path, "r+b") as f:
        f.truncate(0)


def tail(path: Path, lines: int = 300, block: int = 8192) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            pos, data = f.tell(), b""
            while pos > 0 and data.count(b"\n") <= lines:
                step = min(block, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
    except OSError:
        return ""
    return "\n".join(data.decode("utf-8", "replace").splitlines()[-lines:])


def read(src: LogSource, runner: Runner, lines: int = 300) -> str:
    if src.path is not None:
        return tail(src.path, lines)
    r = runner.run(["journalctl", "--user", "-u", unit_name(src.unit or ""), "-n", str(lines),
                    "--no-pager", "-o", "short-iso"])
    return r.out if r.ok else r.text
