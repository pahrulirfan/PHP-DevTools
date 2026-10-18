"""Live service states, shared by the sidebar, the Services page, and the tray menu."""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout

from ..core.controller import Controller
from ..core.services import MYSQL, split_site_process
from .tasks import Tasks
from .widgets import StatusDot, clear_layout, label

UP = ("active", "activating")
DOT = {"active": "on", "activating": "on", "failed": "error"}


def short_name(name: str, db_label: str) -> str:
    """Compact service names for the sidebar."""
    if name.startswith("php"):
        return f"PHP {name[3:]}"
    sp = split_site_process(name)
    if sp:
        from ..core.services import PROCESS_TITLES
        return f"{sp[0]}: {PROCESS_TITLES.get(sp[1], sp[1])}"
    return {"nginx": "Nginx", "dns": "DNS", MYSQL: db_label, "mail": "Mailpit", "redis": "Redis",
            "ngrok": "Sharing (ngrok)", "cloudflared": "Sharing (Cloudflare)"}.get(name, name)


def visible(states: dict[str, str], php_in_use: list[str]) -> dict[str, str]:
    """Hide PHP versions that no site uses while they are stopped: nothing is wrong with them."""
    return {n: s for n, s in states.items()
            if not (n.startswith("php") and n[3:] not in php_in_use and s not in UP + ("failed",))}


class StatusMonitor(QObject):
    """Polls `systemctl --user is-active` in the background and announces changes."""
    changed = Signal(object)   # a dict; Signal(dict) would turn it into a QVariantMap and sort the keys

    def __init__(self, ctl: Controller, tasks: Tasks, parent: QObject | None = None, interval: int = 4000):
        super().__init__(parent)
        self.ctl, self.tasks = ctl, tasks
        self.states: dict[str, str] = {}
        self._busy = False
        self._again = False   # a refresh was asked for while one was running: its answer may be stale
        self._timer = QTimer(self, interval=interval)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def set_interval(self, ms: int) -> None:
        self._timer.setInterval(ms)

    def refresh(self) -> None:
        if self._busy:
            self._again = True
            return
        self._busy, self._again = True, False
        self.tasks.run(self.ctl.service_states, done=self._got, failed=self._failed)

    def refresh_now(self) -> dict[str, str]:
        """Synchronous refresh (one quick systemctl call), for the tray menu that is about to open."""
        try:
            self._got(self.ctl.service_states(), busy=self._busy)
        except Exception:  # noqa: BLE001 - keep the last known states
            pass
        return self.states

    def _failed(self, _e) -> None:
        self._busy = False
        if self._again:
            self.refresh()

    def _got(self, states: dict[str, str], busy: bool = False) -> None:
        self._busy = busy
        if states != self.states:
            self.states = states
            self.changed.emit(states)
        if self._again and not busy:
            self.refresh()

    def running(self) -> int:
        return sum(1 for s in self.states.values() if s in UP)


class SidebarStatus(QFrame):
    """Running services at a glance, under the navigation. A click opens the Services page."""

    def __init__(self, win, monitor: StatusMonitor):
        super().__init__()
        self.win, self.monitor = win, monitor
        self.setObjectName("sideStatus")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Open the Services page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        self.head = label("Services", "muted")
        lay.addWidget(self.head)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(4)
        lay.addLayout(self.rows)
        monitor.changed.connect(self.show_states)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.win.go("Services")
        super().mouseReleaseEvent(e)

    def show_states(self, states: dict[str, str]) -> None:
        clear_layout(self.rows)
        shown = visible(states, self.win.ctl.php_in_use())
        up = sum(1 for s in shown.values() if s in UP)
        self.head.setText(f"Services  ·  {up}/{len(shown)} running" if shown else "Services")
        for name, st in shown.items():
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(StatusDot(DOT.get(st, "off")))
            text = label(short_name(name, self.win.ctl.db_label), None if st in UP else "muted")
            text.setToolTip({"active": "Running", "failed": "Error: see Logs"}.get(st, "Stopped"))
            row.addWidget(text, 1)
            self.rows.addLayout(row)
