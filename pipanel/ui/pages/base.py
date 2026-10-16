from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from ...core.controller import Controller
from ..tasks import Tasks


class Page(QWidget):
    title = ""
    icon_name = "sites"

    def __init__(self, ctl: Controller, tasks: Tasks, win):
        super().__init__()
        self.ctl, self.tasks, self.win = ctl, tasks, win
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("pageBody")
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(32, 8, 32, 32)
        self.body.setSpacing(16)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self.build()
        self.body.addStretch(1)

    def build(self) -> None: ...
    def header_actions(self) -> list[QWidget]: return []
    def refresh(self) -> None: ...

    def run(self, fn, done=None, label: str = "", failed=None, lock: str | None = None) -> None:
        """Run `fn` in the background. Tasks sharing a `lock` run one after another (e.g. "system" for apt)."""
        self.tasks.run(fn, done=done, failed=failed or self.win.error, label=label, lock=lock)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()
