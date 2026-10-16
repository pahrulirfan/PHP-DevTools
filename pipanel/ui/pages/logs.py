from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QComboBox, QPlainTextEdit

from ...core import logs
from ..widgets import button, hbox, label
from .base import Page


class LogsPage(Page):
    title = "Logs"
    icon_name = "logs"

    def build(self) -> None:
        self.source = QComboBox()
        self.source.setMinimumWidth(220)
        self.source.activated.connect(lambda _i: self.load())
        self.auto = QCheckBox("Auto-refresh")
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setProperty("role", "log")
        self.view.setMinimumHeight(420)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.body.addLayout(hbox(self.source, button("Refresh", None, "refresh", self.load), self.auto, None))
        self.body.addWidget(self.view)
        self._timer = QTimer(self, interval=2000)
        self._timer.timeout.connect(lambda: self.auto.isChecked() and self.load())

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._timer.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._timer.stop()

    def refresh(self) -> None:
        cur = self.source.currentText()
        self._srcs = logs.sources(self.ctl.paths, [s.name for s in self.ctl.sites()], self.ctl.installed_php())
        self.source.clear()
        for s in self._srcs:
            self.source.addItem(s.label)
        if cur:
            self.source.setCurrentText(cur)
        self.load()

    def load(self) -> None:
        i = self.source.currentIndex()
        if i < 0:
            return
        src = self._srcs[i]
        self.run(lambda: logs.read(src, self.ctl.runner), done=self._show)

    def _show(self, text: str) -> None:
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.view.setPlainText(text or "(empty)")
        if at_end:
            bar.setValue(bar.maximum())
