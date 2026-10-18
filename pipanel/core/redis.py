"""User-owned Redis (cache, queues, sessions, Horizon) on 127.0.0.1:6379."""
from __future__ import annotations

import socket
from pathlib import Path

from . import templates as T
from .paths import Paths

SERVER = "/usr/bin/redis-server"
PORT = 6379


def cygwin_path(path) -> str:
    """Redis for Windows is a Cygwin/MSYS2 build: it reads /cygdrive/c/Users/... instead of C:\\Users\\..."""
    from . import platform
    if not platform.is_windows():
        return str(path)
    import os
    p = os.path.realpath(str(path)) if os.name == "nt" else str(path)
    drive, rest = os.path.splitdrive(p) if os.name == "nt" else (p[:2] if p[1:2] == ":" else "", p[2:] if p[1:2] == ":" else p)
    rest = rest.replace("\\", "/")
    return f"/cygdrive/{drive[0].lower()}{rest}" if drive else rest


class Redis:
    def __init__(self, paths: Paths, *, server: str = SERVER, port: int = PORT):
        self.paths, self.server, self.port = paths, server, port

    def available(self) -> bool:
        """Is the redis-server package installed? (Installed through the root helper.)"""
        return Path(self.server).exists()

    @property
    def conf(self) -> Path:
        return self.paths.config / "redis" / "redis.conf"

    @property
    def data_dir(self) -> Path:
        return self.paths.data / "redis"

    def write_config(self) -> bool:
        text = T.render_redis_conf(self.paths, port=self.port, path=cygwin_path)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.conf.exists() and self.conf.read_text() == text:
            return False
        self.conf.parent.mkdir(parents=True, exist_ok=True)
        self.conf.write_text(text)
        return True

    def unit_spec(self) -> dict:
        return dict(description="PIPanel: Redis", argv=[str(self.server), cygwin_path(self.conf)],
                    dirs=[str(self.paths.run), str(self.data_dir)])

    def ping(self, timeout: float = 1.0) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as s:
                s.sendall(b"PING\r\n")
                return s.recv(16).startswith(b"+PONG")
        except OSError:
            return False

    def env(self) -> dict[str, str]:
        """Laravel .env connection values (the stores that use Redis are left as they are)."""
        return {"REDIS_HOST": "127.0.0.1", "REDIS_PORT": str(self.port), "REDIS_PASSWORD": "null"}
