"""Activity panel: every running, waiting, and recently finished task, so none of them gets lost."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from . import theme
from .icons import icon
from .tasks import DONE, FAILED, QUEUED, RUNNING, TaskInfo, Tasks
from .widgets import button, clear_layout, icon_button, label

AUTO_DISMISS_MS = 10_000   # finished tasks leave the list by themselves; failed ones stay until dismissed


class ActivityPanel(QWidget):
    def __init__(self, tasks: Tasks, show_details):
        super().__init__()
        self.setObjectName("activity")
        self.setAttribute(Qt.WA_StyledBackground, True)   # a plain QWidget ignores QSS backgrounds otherwise
        self.tasks, self.show_details = tasks, show_details
        self._scheduled: set[int] = set()
        self.rows = QVBoxLayout(self)
        self.rows.setContentsMargins(12, 8, 12, 8)
        self.rows.setSpacing(6)
        tasks.changed.connect(self.rebuild)
        theme.bus.changed.connect(lambda _t: self.rebuild())
        self.setVisible(False)

    def rebuild(self) -> None:
        clear_layout(self.rows)
        infos = list(self.tasks.infos.values())
        self.setVisible(bool(infos))
        for info in infos:
            self.rows.addLayout(self._row(info))
            if info.state == DONE and info.id not in self._scheduled:
                self._scheduled.add(info.id)
                QTimer.singleShot(AUTO_DISMISS_MS, lambda i=info.id: self._auto_dismiss(i))

    def _auto_dismiss(self, jid: int) -> None:
        self._scheduled.discard(jid)
        info = self.tasks.infos.get(jid)
        if info and info.state == DONE:
            self.tasks.dismiss(jid)

    def _row(self, info: TaskInfo) -> QHBoxLayout:
        t = theme.tokens()
        row = QHBoxLayout()
        row.setSpacing(10)
        mark = QLabel()
        mark.setFixedWidth(18)
        text = label(info.label.rstrip("…").rstrip("."))
        status = label("", "muted")
        if info.state == RUNNING:
            bar = QProgressBar()
            bar.setRange(0, 0)
            bar.setTextVisible(False)
            bar.setFixedWidth(80)
            row.addWidget(mark)    # empty slot keeps the text aligned with the other rows
            row.addWidget(text)
            row.addStretch(1)
            row.addWidget(status)
            status.setText("Running")
            row.addWidget(bar)
            return row
        if info.state == QUEUED:
            mark.setPixmap(icon("refresh", t["text-muted"], 14).pixmap(14, 14))
            status.setText("Waiting for another installation to finish")
        elif info.state == DONE:
            mark.setPixmap(icon("check", t["success-text"], 16).pixmap(16, 16))
            status.setText("Done")
        else:
            mark.setPixmap(icon("alert", t["danger-text"], 16).pixmap(16, 16))
            status.setText("Failed")
        row.insertWidget(0, mark)
        row.addWidget(text)
        row.addStretch(1)
        row.addWidget(status)
        if info.state == FAILED and info.message:
            row.addWidget(button("Details", "link", on_click=lambda m=info.message, n=info.label: self.show_details(
                f"{n.rstrip('…')} failed:\n\n{m}")))
        if info.state in (DONE, FAILED):
            b = icon_button("x", "Dismiss", lambda i=info.id: self.tasks.dismiss(i))
            b.setFixedSize(26, 26)
            row.addWidget(b)
        return row
