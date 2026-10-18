"""How services are run: systemd user units on Linux, the PIPanel agent on Windows and macOS.

A service is described by a spec, the same on every system:

    description   human name
    argv          the command, as a list (paths may contain spaces)
    env           extra environment variables
    workdir       working directory
    dirs          folders to create before starting
    reload        argv that reloads it, "signal:USR2", or None (reload = restart)

The rest of PIPanel only calls the methods below, so it never needs to know which one is in use.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path

from . import platform
from . import templates as T
from .paths import Paths
from .runner import Result, Runner

# Real tests run against the user's own service manager, so they must use unit names of their own
# (PIPANEL_UNIT_PREFIX=pipanel-e2e) and never touch the services of a real installation.
UNIT_PREFIX = os.environ.get("PIPANEL_UNIT_PREFIX", "pipanel")
VERBS = ("start", "stop", "restart", "reload", "enable", "disable")


def unit_name(service: str) -> str:
    return f"{UNIT_PREFIX}-{service}.service"


def path_unit_name(service: str) -> str:
    return f"{UNIT_PREFIX}-{service}.path"


def cmdline(argv: list[str]) -> str:
    """One command line for a unit file: quoted where needed, with systemd's % and $ escaped."""
    return " ".join(shlex.quote(str(a)).replace("%", "%%").replace("$", "$$") for a in argv)


class Supervisor:
    """Interface. `status` answers active / activating / inactive / failed / not-found."""

    def write(self, name: str, spec: dict) -> bool: raise NotImplementedError
    def remove(self, name: str) -> None: raise NotImplementedError
    def has(self, name: str) -> bool: raise NotImplementedError
    def names(self) -> list[str]: raise NotImplementedError
    def ctl(self, verb: str, *names: str) -> Result: raise NotImplementedError
    def status(self, names: list[str]) -> dict[str, str]: raise NotImplementedError
    def journal(self, name: str, lines: int = 300) -> str: raise NotImplementedError

    def set_watch(self, folders: list[str], argv: list[str], enabled: bool) -> None:
        """Run `argv` whenever an entry is added to or removed from one of `folders` (none: stop watching)."""
        raise NotImplementedError

    def watch_enabled(self, on: bool) -> None: ...

    def autostart(self, on: bool) -> None:
        """Bring the services back at login (systemd does it per unit: nothing to do there)."""


# ---------------------------------------------------------------- Linux: systemd --user
class SystemdSupervisor(Supervisor):
    WATCH = "watch"

    def __init__(self, paths: Paths, runner: Runner):
        self.paths, self.runner = paths, runner

    def _file(self, name: str) -> Path:
        return self.paths.systemd_user / unit_name(name)

    def render(self, spec: dict) -> str:
        reload = spec.get("reload")
        if reload == "signal:USR2":
            reload = "/bin/kill -USR2 $MAINPID"
        elif reload:
            reload = cmdline(reload)
        dirs = [str(d) for d in spec.get("dirs") or []]
        return T.render_unit(description=spec["description"], exec_start=cmdline(spec["argv"]), exec_reload=reload,
                             env=spec.get("env"), workdir=spec.get("workdir"),
                             exec_pre=[cmdline(["/bin/mkdir", "-p", *dirs])] if dirs else [])

    def write(self, name: str, spec: dict) -> bool:
        f, text = self._file(name), self.render(spec)
        if f.exists() and f.read_text() == text:
            return False
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        self.runner.run(["systemctl", "--user", "daemon-reload"])
        return True

    def write_many(self, specs: dict[str, dict]) -> bool:
        """Several units with one daemon-reload."""
        changed = False
        for name, spec in specs.items():
            f, text = self._file(name), self.render(spec)
            if not (f.exists() and f.read_text() == text):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text)
                changed = True
        if changed:
            self.runner.run(["systemctl", "--user", "daemon-reload"])
        return changed

    def remove(self, name: str) -> None:
        f = self._file(name)
        if f.exists():
            f.unlink()
            self.runner.run(["systemctl", "--user", "daemon-reload"])

    def has(self, name: str) -> bool:
        return self._file(name).exists()

    def names(self) -> list[str]:
        head = f"{UNIT_PREFIX}-"
        found = (f.name[len(head):-len(".service")] for f in self.paths.systemd_user.glob(f"{head}*.service"))
        return sorted(n for n in found if not n.startswith("e2e-"))   # units of the real tests are not ours

    def ctl(self, verb: str, *names: str) -> Result:
        if verb == "reload":   # nginx -s reload / USR2; a unit without ExecReload is restarted
            r = self.runner.run(["systemctl", "--user", "reload", *[unit_name(n) for n in names]])
            return r if r.ok else self.runner.run(["systemctl", "--user", "restart", *[unit_name(n) for n in names]])
        return self.runner.run(["systemctl", "--user", verb, *[unit_name(n) for n in names]])

    def status(self, names: list[str]) -> dict[str, str]:
        if not names:
            return {}
        r = self.runner.run(["systemctl", "--user", "is-active", *[unit_name(n) for n in names]])
        lines = r.out.split()
        states = {n: (lines[i] if i < len(lines) else "unknown") for i, n in enumerate(names)}
        for n in names:   # a unit that was never written is reported "inactive" by is-active
            if not self.has(n) and states[n] == "inactive":
                states[n] = "not-found"
        return states

    def journal(self, name: str, lines: int = 300) -> str:
        r = self.runner.run(["journalctl", "--user", "-u", unit_name(name), "-n", str(lines), "--no-pager",
                             "-o", "short-iso"])
        return r.out if r.ok else r.text

    def last_lines(self, name: str, lines: int = 8) -> str:
        return self.runner.run(["journalctl", "--user", "-u", unit_name(name), "-n", str(lines), "--no-pager",
                                "-o", "cat"]).out

    # a systemd path unit (inotify) starts the sync service; it works while the app is closed
    def set_watch(self, folders: list[str], argv: list[str], enabled: bool, env: dict | None = None) -> None:
        from .watch import render_path_unit
        f = self.paths.systemd_user / path_unit_name(self.WATCH)
        if not folders:
            if f.exists():
                self.runner.run(["systemctl", "--user", "disable", "--now", f.name])
                f.unlink()
                self.remove(self.WATCH)
            return
        self.write(self.WATCH, dict(description="PIPanel: serve new project folders", argv=argv, env=env))
        text = render_path_unit(folders)
        if f.exists() and f.read_text() == text:
            return
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        self.runner.run(["systemctl", "--user", "daemon-reload"])
        self.runner.run(["systemctl", "--user", "enable" if enabled else "disable", f.name])
        self.runner.run(["systemctl", "--user", "restart", f.name])   # watch the new list of folders now

    def watch_enabled(self, on: bool) -> None:
        f = self.paths.systemd_user / path_unit_name(self.WATCH)
        if f.exists():
            self.runner.run(["systemctl", "--user", "enable" if on else "disable", f.name])


# ---------------------------------------------------------------- Windows, macOS: the PIPanel agent
class AgentSupervisor(Supervisor):
    """Talks to the agent through files: a spec per service, a command file per request, one status file.
    The agent is started on demand (and at login when autostart is on)."""
    SETTLE = 6.0   # how long a start/stop waits for the agent to report the new state

    def __init__(self, paths: Paths, *, spawn=subprocess.Popen, start_agent: bool = True):
        self.paths, self.spawn, self.start_agent = paths, spawn, start_agent

    @property
    def units(self) -> Path:
        return self.paths.config / "units"

    @property
    def control(self) -> Path:
        return self.paths.state / "agent"

    def _file(self, name: str) -> Path:
        return self.units / f"{name}.json"

    def write(self, name: str, spec: dict) -> bool:
        text = json.dumps({k: (str(v) if isinstance(v, Path) else v) for k, v in spec.items()},
                          indent=2, default=str) + "\n"
        f = self._file(name)
        if f.exists() and f.read_text() == text:
            return False
        self.units.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return True

    def write_many(self, specs: dict[str, dict]) -> bool:
        changed = False
        for name, spec in specs.items():
            changed |= self.write(name, spec)
        return changed

    def remove(self, name: str) -> None:
        self._file(name).unlink(missing_ok=True)
        (self.units / f"{name}.enabled").unlink(missing_ok=True)

    def has(self, name: str) -> bool:
        return self._file(name).exists()

    def names(self) -> list[str]:
        return sorted(f.stem for f in self.units.glob("*.json")) if self.units.is_dir() else []

    # ---- the agent process ----
    def _state(self) -> dict:
        try:
            return json.loads((self.control / "status.json").read_text())
        except (OSError, ValueError):
            return {}

    def agent_alive(self) -> bool:
        st = self._state()
        return bool(st) and time.time() - st.get("heartbeat", 0) < 5

    def ensure_agent(self) -> None:
        if self.agent_alive() or not self.start_agent:
            return
        self.control.mkdir(parents=True, exist_ok=True)
        log = open(self.paths.logs / "agent.log", "ab") if self.paths.logs.is_dir() else subprocess.DEVNULL
        kw: dict = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True)
        if platform.is_windows():
            kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED, NEW_PROCESS_GROUP, NO_WINDOW
        else:
            kw["start_new_session"] = True
        self.spawn([*platform.self_command(), "_agent"], **kw)
        end = time.monotonic() + 10
        while time.monotonic() < end and not self.agent_alive():
            time.sleep(0.1)

    def _send(self, name: str, verb: str) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        tmp = self.control / f".{name}.cmd.tmp"
        tmp.write_text(verb)
        os.replace(tmp, self.control / f"{name}.cmd")

    def ctl(self, verb: str, *names: str) -> Result:
        if verb not in VERBS:
            return Result(1, "", f"unknown verb: {verb}")
        if verb in ("enable", "disable"):
            for n in names:
                flag = self.units / f"{n}.enabled"
                flag.touch() if verb == "enable" and self.has(n) else flag.unlink(missing_ok=True)
            return Result(0)
        missing = [n for n in names if not self.has(n)]
        if missing and verb != "stop":
            return Result(5, "", f"Unit {', '.join(missing)} not found.")
        self.ensure_agent()
        if not self.agent_alive():
            return Result(1, "", "the PIPanel agent did not start (see the agent log)")
        for n in names:
            self._send(n, verb)
        want = {"start": ("active", "activating", "failed"), "restart": ("active", "activating", "failed"),
                "reload": ("active", "activating", "failed", "inactive"), "stop": ("inactive", "not-found")}[verb]
        end = time.monotonic() + self.SETTLE
        while time.monotonic() < end:
            st = self.status(list(names))
            if all(st[n] in want for n in names) and not any((self.control / f"{n}.cmd").exists() for n in names):
                break
            time.sleep(0.1)
        return Result(0)

    def status(self, names: list[str]) -> dict[str, str]:
        st = self._state()
        alive = bool(st) and time.time() - st.get("heartbeat", 0) < 5
        units = st.get("units", {}) if alive else {}
        return {n: (units.get(n, {}).get("state", "inactive") if self.has(n) else "not-found") for n in names}

    def journal(self, name: str, lines: int = 300) -> str:
        from .logs import tail
        return tail(self.paths.logs / "units" / f"{name}.log", lines)

    def last_lines(self, name: str, lines: int = 8) -> str:
        return self.journal(name, lines)

    def autostart(self, on: bool) -> None:
        from . import autostart
        autostart.set_login(autostart.AGENT, on)

    def set_watch(self, folders: list[str], argv: list[str], enabled: bool, env: dict | None = None) -> None:
        f = self.units / "watch.folders"
        text = json.dumps({"folders": folders, "argv": argv}, indent=2) + "\n"
        if not folders:
            f.unlink(missing_ok=True)
        elif not f.exists() or f.read_text() != text:
            self.units.mkdir(parents=True, exist_ok=True)
            f.write_text(text)


def for_platform(paths: Paths, runner: Runner) -> Supervisor:
    return AgentSupervisor(paths) if platform.current() in (platform.WINDOWS, platform.MACOS) \
        else SystemdSupervisor(paths, runner)
