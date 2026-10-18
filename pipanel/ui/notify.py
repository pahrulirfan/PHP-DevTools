"""Tell the developer when a service stops working, with the reason and a fix, instead of letting them find out
from an error page in the browser."""
from __future__ import annotations

import shutil
import subprocess
import threading

from PySide6.QtCore import QObject, Signal

from ..core.controller import Controller
from ..core.services import service_title
from .status import StatusMonitor

ACTIONS = (("restart", "Restart"), ("details", "Details"))


def broke(old: str | None, new: str) -> bool:
    """A crash, not a stop: systemd marks a crashed unit failed, or restarts it (active -> activating). A service
    that the user stops becomes inactive and is not reported."""
    return new == "failed" or (old == "active" and new == "activating")


class Notifier(QObject):
    """Desktop notifications with buttons through notify-send; the tray balloon where that is missing."""
    action = Signal(str, str)   # service, action key

    def __init__(self, tray_icon=None, parent: QObject | None = None, spawn=subprocess.run):
        super().__init__(parent)
        self.tray_icon, self.spawn = tray_icon, spawn
        self.has_notify_send = shutil.which("notify-send") is not None

    def show(self, service: str, title: str, body: str) -> None:
        if not self.has_notify_send:
            if self.tray_icon is not None:
                self.tray_icon.showMessage(title, body)
            return
        argv = ["notify-send", "-a", "PIPanel", "-i", "dialog-warning", "-u", "critical",
                *[f"--action={k}={text}" for k, text in ACTIONS], title, body]

        def wait() -> None:   # notify-send prints the chosen action and exits when the notification closes
            try:
                r = self.spawn(argv, capture_output=True, text=True, timeout=3600)
            except (OSError, subprocess.SubprocessError):
                return
            key = (r.stdout or "").strip()
            if key in dict(ACTIONS):
                self.action.emit(service, key)
        threading.Thread(target=wait, daemon=True).start()


class Watchdog(QObject):
    """Watches the service states and reports a service that breaks, once per breakdown."""
    broken = Signal(str, str)   # service, explanation (for the in-app banner)

    def __init__(self, ctl: Controller, monitor: StatusMonitor, win, notifier: Notifier):
        super().__init__(win)
        self.ctl, self.monitor, self.win, self.notifier = ctl, monitor, win, notifier
        self.last: dict[str, str] = {}
        self.reported: set[str] = set()
        monitor.changed.connect(self.check)
        notifier.action.connect(self.act)

    def check(self, states: dict[str, str]) -> None:
        for name, new in states.items():
            old = self.last.get(name)
            if new == "active":
                self.reported.discard(name)   # working again: a later breakdown is reported again
            elif broke(old, new) and name not in self.reported:
                self.reported.add(name)
                self.win.tasks.run(lambda n=name: self.ctl.failure_report(n),
                                   done=lambda text, n=name: self.report(n, text), failed=lambda _e: None)
        self.last = dict(states)

    def report(self, name: str, text: str) -> None:
        title = f"{service_title(name, self.ctl.db_label)} stopped working"
        self.broken.emit(name, text)
        if self.ctl.cfg.notify:
            self.notifier.show(name, title, text)

    def act(self, name: str, key: str) -> None:
        if key == "restart":
            def done(r):
                if not r.ok:
                    self.win.error(r.text)
                self.monitor.refresh()
            self.win.tasks.run(lambda: self.ctl.restart_service(name), done=done,
                               label=f"Restarting {service_title(name, self.ctl.db_label)}…")
        else:
            self.win.show_problem(name)
