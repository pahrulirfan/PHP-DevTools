"""Log sources and file tailing."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import Paths
from .runner import Runner
from .services import unit_name


@dataclass(frozen=True)
class LogSource:
    label: str
    path: Path | None = None
    unit: str | None = None   # service name (without pipanel-), read via journalctl


def sources(paths: Paths, site_names: list[str], php_versions: list[str]) -> list[LogSource]:
    out = [LogSource("Nginx (error)", unit="nginx"), LogSource("DNS (dnsmasq)", unit="dns")]
    out += [LogSource(f"PHP-FPM {v}", path=paths.logs / f"php{v}-fpm.log") for v in php_versions]
    out += [LogSource("Database server", path=paths.logs / "mysql.log"), LogSource("ngrok", unit="ngrok")]
    for n in site_names:
        out.append(LogSource(f"{n} (access)", path=paths.logs / f"{n}-access.log"))
        out.append(LogSource(f"{n} (error)", path=paths.logs / f"{n}-error.log"))
    return out


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
