"""User-owned Redis (cache, queues, sessions, Horizon) on 127.0.0.1:6379."""
from __future__ import annotations

import socket
from pathlib import Path

from . import templates as T
from .paths import Paths

SERVER = "/usr/bin/redis-server"
PORT = 6379


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
        text = T.render_redis_conf(self.paths, port=self.port)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.conf.exists() and self.conf.read_text() == text:
            return False
        self.conf.parent.mkdir(parents=True, exist_ok=True)
        self.conf.write_text(text)
        return True

    def unit_spec(self) -> dict:
        return dict(description="PIPanel: Redis", exec_start=f"{self.server} {self.conf}",
                    exec_pre=[f"/bin/mkdir -p {self.paths.run} {self.data_dir}"])

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
