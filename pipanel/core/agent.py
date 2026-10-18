"""The PIPanel agent: what systemd does for PIPanel on Linux, for Windows and macOS.

It runs in the background (`pipanel _agent`), started by the app when needed and at login when autostart is on:
- starts, stops, restarts and reloads the services described in <config>/units/*.json, on request;
- restarts a service that exits on its own (after 2 s; five failures within 30 s mark it failed);
- writes everything a service prints to <state>/logs/units/<name>.log;
- starts the services marked enabled when it starts (login);
- watches the projects folders and runs `pipanel sync --settle` when a folder appears or disappears.

Requests and answers are files (see supervisor.AgentSupervisor), so the app, the terminal command and the agent
need no other connection.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import platform
from .paths import Paths

RESTART_DELAY = 2.0
BURST, BURST_WINDOW = 5, 30.0
STOP_TIMEOUT = 6.0
LOG_LIMIT = 5 * 1024 * 1024


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if platform.is_windows():
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                           creationflags=0x08000000)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def replace(tmp: Path, target: Path, attempts: int = 50) -> None:
    """os.replace that waits while Windows refuses because a reader has the file open."""
    for i in range(attempts):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.02)


def kill_tree(pid: int, timeout: float = STOP_TIMEOUT, proc: subprocess.Popen | None = None) -> None:
    """Stop a service and everything it started (nginx workers, php-cgi children, npm's node). `proc` is the
    Popen of our own child, which is reaped while waiting (a dead child is otherwise still 'alive')."""
    if platform.is_windows():
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    def running() -> bool:
        return proc.poll() is None if proc is not None else alive(pid)
    end = time.monotonic() + timeout
    while time.monotonic() < end and running():
        time.sleep(0.05)
    if running():
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


@dataclass
class Unit:
    name: str
    spec: dict
    proc: subprocess.Popen | None = None
    pid: int = 0                       # also for a process adopted from an earlier agent
    want: bool = False                 # should be running
    state: str = "inactive"
    failures: list[float] = field(default_factory=list)
    restart_at: float = 0.0


class Agent:
    def __init__(self, paths: Paths, *, poll: float = 0.25):
        self.paths, self.poll = paths, poll
        self.units_dir = paths.config / "units"
        self.control = paths.state / "agent"
        self.logs = paths.logs / "units"
        self.units: dict[str, Unit] = {}
        self.watch_sig: tuple | None = None
        self.watch_proc: subprocess.Popen | None = None
        self.watch_dirty_at = 0.0
        self.running = True

    # ---- start-up ----
    def lock(self) -> bool:
        """Only one agent per user. False when another one is alive."""
        self.control.mkdir(parents=True, exist_ok=True)
        f = self.control / "agent.pid"
        try:
            other = int(f.read_text())
        except (OSError, ValueError):
            other = 0
        if other and other != os.getpid() and alive(other):
            return False
        f.write_text(str(os.getpid()))
        return True

    def adopt(self) -> None:
        """Services started by a previous agent that are still running are taken over, not started twice."""
        try:
            old = json.loads((self.control / "status.json").read_text()).get("units", {})
        except (OSError, ValueError):
            old = {}
        for name, info in old.items():
            pid = int(info.get("pid") or 0)
            if pid and alive(pid) and name in self.units:
                u = self.units[name]
                u.pid, u.want, u.state = pid, True, "active"

    def load(self) -> None:
        names = {f.stem for f in self.units_dir.glob("*.json")} if self.units_dir.is_dir() else set()
        for name in names:
            try:
                spec = json.loads((self.units_dir / f"{name}.json").read_text())
            except (OSError, ValueError):
                continue
            if name in self.units:
                self.units[name].spec = spec   # used at the next start
            else:
                self.units[name] = Unit(name, spec)
        for name in list(self.units):
            if name not in names and not self.units[name].pid:
                del self.units[name]

    # ---- processes ----
    def _log(self, name: str):
        self.logs.mkdir(parents=True, exist_ok=True)
        f = self.logs / f"{name}.log"
        if f.exists() and f.stat().st_size > LOG_LIMIT:
            f.replace(f.with_suffix(".log.1"))
        return open(f, "ab")

    def spawn(self, u: Unit) -> None:
        spec = u.spec
        for d in spec.get("dirs") or []:
            Path(d).mkdir(parents=True, exist_ok=True)
        env = {**os.environ, **(spec.get("env") or {})}
        kw: dict = dict(cwd=spec.get("workdir") or None, env=env, stdin=subprocess.DEVNULL, close_fds=True)
        if platform.is_windows():
            kw["creationflags"] = 0x00000200 | 0x08000000   # NEW_PROCESS_GROUP, NO_WINDOW
        else:
            kw["start_new_session"] = True
        with self._log(u.name) as log:
            log.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} starting\n".encode())
            log.flush()
            try:
                u.proc = subprocess.Popen([str(a) for a in spec["argv"]], stdout=log, stderr=subprocess.STDOUT,
                                          **kw)
            except OSError as e:
                log.write(f"cannot start: {e}\n".encode())
                u.proc, u.pid = None, 0
                self._failed(u)
                return
        u.pid, u.state = u.proc.pid, "active"

    def stop(self, u: Unit) -> None:
        u.want = False
        if u.pid:
            kill_tree(u.pid, proc=u.proc)
            if u.proc is not None:
                try:
                    u.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        u.proc, u.pid, u.state, u.failures = None, 0, "inactive", []

    def reload(self, u: Unit) -> None:
        how = u.spec.get("reload")
        if not u.pid:
            return
        if how == "signal:USR2" and not platform.is_windows():
            os.kill(u.pid, signal.SIGUSR2)
        elif isinstance(how, list):
            subprocess.run([str(a) for a in how], capture_output=True, env={**os.environ, **(u.spec.get("env") or {})},
                           creationflags=0x08000000 if platform.is_windows() else 0)
        else:
            self.stop(u)
            u.want = True
            self.spawn(u)

    def _failed(self, u: Unit) -> None:
        now = time.monotonic()
        u.failures = [t for t in u.failures if now - t < BURST_WINDOW] + [now]
        if len(u.failures) >= BURST:
            u.want, u.state = False, "failed"
        else:
            u.state, u.restart_at = "activating", now + RESTART_DELAY

    def reap(self) -> None:
        for u in self.units.values():
            if not u.pid:
                if u.want and u.state == "activating" and time.monotonic() >= u.restart_at:
                    self.spawn(u)
                continue
            if u.proc is not None:
                code = u.proc.poll()
                if code is None:
                    continue
            elif alive(u.pid):   # adopted
                continue
            else:
                code = 1
            u.proc, u.pid = None, 0
            if not u.want:
                u.state = "inactive"
            elif code == 0 and u.spec.get("oneshot"):
                u.want, u.state = False, "inactive"
            else:
                self._failed(u)

    # ---- requests ----
    def commands(self) -> None:
        for f in sorted(self.control.glob("*.cmd")):
            name, verb = f.stem, f.read_text().strip()
            f.unlink(missing_ok=True)
            self.load()
            u = self.units.get(name)
            if u is None:
                continue
            if verb == "start" and not u.pid:
                u.want, u.failures = True, []
                self.spawn(u)
            elif verb == "stop":
                self.stop(u)
            elif verb == "restart":
                self.stop(u)
                u.want = True
                self.spawn(u)
            elif verb == "reload":
                self.reload(u)

    def start_enabled(self) -> None:
        for u in self.units.values():
            if (self.units_dir / f"{u.name}.enabled").exists() and not u.pid:
                u.want = True
                self.spawn(u)

    # ---- projects folders ----
    def watch(self) -> None:
        f = self.units_dir / "watch.folders"
        try:
            cfg = json.loads(f.read_text())
        except (OSError, ValueError):
            self.watch_sig = None
            return
        sig = []
        for folder in cfg.get("folders", []):
            try:
                sig.append((folder, tuple(sorted(e.name for e in os.scandir(folder)))))
            except OSError:
                sig.append((folder, ()))
        sig = tuple(sig)
        if self.watch_sig is None:
            self.watch_sig = sig
            return
        now = time.monotonic()
        if sig != self.watch_sig:
            self.watch_sig, self.watch_dirty_at = sig, now
        busy = self.watch_proc is not None and self.watch_proc.poll() is None
        if self.watch_dirty_at and now - self.watch_dirty_at > 0.5 and not busy:
            self.watch_dirty_at = 0.0
            kw: dict = dict(stdin=subprocess.DEVNULL, close_fds=True)
            if platform.is_windows():
                kw["creationflags"] = 0x08000000
            with self._log("watch") as log:
                self.watch_proc = subprocess.Popen(cfg["argv"], stdout=log, stderr=subprocess.STDOUT, **kw)

    def write_status(self) -> None:
        data = {"heartbeat": time.time(), "pid": os.getpid(),
                "units": {n: {"state": u.state, "pid": u.pid} for n, u in self.units.items()}}
        tmp = self.control / "status.json.tmp"
        tmp.write_text(json.dumps(data))
        replace(tmp, self.control / "status.json")

    # ---- main loop ----
    def step(self) -> None:
        self.load()
        self.commands()
        self.reap()
        self.watch()
        self.write_status()

    def run(self) -> int:
        if not self.lock():
            print("another PIPanel agent is running")
            return 0
        self.load()
        self.adopt()
        self.start_enabled()
        import threading
        if not platform.is_windows() and threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        while self.running:
            self.step()
            time.sleep(self.poll)
        return 0


def main(paths: Paths | None = None) -> int:
    paths = paths or Paths.from_env()
    paths.ensure()
    return Agent(paths).run()


if __name__ == "__main__":
    sys.exit(main())
