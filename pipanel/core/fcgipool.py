"""PHP for Nginx on Windows, where PHP-FPM does not exist: a few php-cgi.exe processes, each on its own port, kept
running (php-cgi exits after PHP_FCGI_MAX_REQUESTS requests on purpose, and is started again). Nginx spreads the
requests over them through an upstream block.

    pipanel _fcgi --php C:\\...\\php-cgi.exe --port 9830 --workers 4
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

MAX_REQUESTS = "500"


def ports(base: int, workers: int) -> list[int]:
    return [base + i for i in range(workers)]


def run(php_cgi: str, base: int, workers: int, *, sleep=time.sleep, spawn=subprocess.Popen, rounds: int = -1) -> int:
    env = {**os.environ, "PHP_FCGI_MAX_REQUESTS": MAX_REQUESTS, "PHP_FCGI_CHILDREN": "0"}
    kw = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}
    procs: dict[int, subprocess.Popen] = {}
    try:
        while rounds != 0:
            for port in ports(base, workers):
                p = procs.get(port)
                if p is None or p.poll() is not None:
                    procs[port] = spawn([php_cgi, "-b", f"127.0.0.1:{port}"], env=env, stdin=subprocess.DEVNULL, **kw)
            sleep(0.5)
            rounds -= 1
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.kill()
    return 0


def main(argv: list[str]) -> int:
    import argparse
    a = argparse.ArgumentParser(prog="pipanel _fcgi")
    a.add_argument("--php", required=True)
    a.add_argument("--port", type=int, required=True)
    a.add_argument("--workers", type=int, default=4)
    o = a.parse_args(argv)
    return run(o.php, o.port, o.workers)
