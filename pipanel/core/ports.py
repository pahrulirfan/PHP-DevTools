"""Who is using a TCP port? Names Docker containers (DDEV, Laragon-style stacks) that root-owned proxies hide."""
from __future__ import annotations

import re

from .runner import Runner

SS_PROC = re.compile(r'\(\("([^"]+)"')


def listeners(runner: Runner) -> dict[int, str]:
    """`ss -ltnp` -> {port: process name ('' when it belongs to another user)}."""
    res: dict[int, str] = {}
    for line in runner.run(["ss", "-H", "-ltnp"]).out.splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        m = re.search(r":(\d+)$", cols[3])
        if m:
            p = SS_PROC.search(line)
            res[int(m.group(1))] = p.group(1) if p else ""
    return res


def docker_publishers(runner: Runner) -> dict[int, str]:
    """{host port: container name} for published Docker ports (empty if Docker is missing or not accessible)."""
    r = runner.run(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"], timeout=8)
    out: dict[int, str] = {}
    if not r.ok:
        return out
    for line in r.out.splitlines():
        name, _, ports = line.partition("\t")
        for m in re.finditer(r"(?:[\d.]+|\[::\]):(\d+)(?:-(\d+))?->", ports):   # 80->80 or 8025-8026->8025-8026
            first = int(m.group(1))
            for port in range(first, int(m.group(2) or first) + 1):
                out.setdefault(port, name)
    return out


def owner(runner: Runner, port: int, known: dict[int, str] | None = None) -> str:
    """A human description of what holds `port`, or "" when nothing does."""
    known = listeners(runner) if known is None else known
    if port not in known:
        return ""
    container = docker_publishers(runner).get(port)
    if container:
        extra = " (DDEV)" if container.startswith("ddev-") else ""
        return f"the Docker container {container}{extra}"
    return known[port] or "another program"


def advice(name: str) -> str:
    """What to do about it."""
    if "ddev" in name:
        return "Stop DDEV with `ddev poweroff`, or move PIPanel to other ports in Settings."
    if "Docker" in name:
        return "Stop that container, or move PIPanel to other ports in Settings."
    return "Stop that program, or move PIPanel to other ports in Settings."
