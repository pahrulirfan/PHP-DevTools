from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QComboBox, QPlainTextEdit

from ...core import logs
from ...core.services import MYSQL
from ...core.sites import LARAVEL
from ..widgets import button, hbox, label

LEVEL_CHOICES = [("All levels", "debug"), ("Warnings and worse", "warning"), ("Errors and worse", "error")]
from .base import Page


class LogsPage(Page):
    title = "Logs"
    icon_name = "logs"

    def build(self) -> None:
        self.source = QComboBox()
        self.source.setMinimumWidth(220)
        self.source.activated.connect(lambda _i: self.load())
        self.auto = QCheckBox("Auto-refresh")
        self.level = QComboBox()
        self.level.setAccessibleName("Minimum level")
        for text, key in LEVEL_CHOICES:
            self.level.addItem(text, key)
        self.level.activated.connect(lambda _i: self.load())
        self.clear_btn = button("Clear", None, "trash", self.clear, tooltip="Empty this log file")
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setProperty("role", "log")
        self.view.setMinimumHeight(420)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.body.addLayout(hbox(self.source, self.level, button("Refresh", None, "refresh", self.load), self.auto,
                                 None, self.clear_btn))
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
        sites = self.ctl.sites()
        units = [u for u in self.ctl.services.unit_services()
                 if u not in ("nginx", "dns", MYSQL, "ngrok") and not u.startswith("php")]
        self._srcs = logs.sources(self.ctl.paths, [s.name for s in sites], self.ctl.installed_php(),
                                  laravel={s.name: s.path for s in sites if s.kind == LARAVEL}, units=units)
        self.source.clear()
        for s in self._srcs:
            self.source.addItem(s.label)
        if cur:
            self.source.setCurrentText(cur)
        self.load()

    def show_site(self, site: str) -> None:
        """The most useful log of a site: laravel.log for Laravel, otherwise its error log."""
        self.refresh()
        for want in (f"{site} (laravel.log)", f"{site} (error)"):
            i = self.source.findText(want)
            if i >= 0:
                self.source.setCurrentIndex(i)
                self.load()
                return

    def current(self):
        i = self.source.currentIndex()
        return self._srcs[i] if 0 <= i < len(getattr(self, "_srcs", [])) else None

    def load(self) -> None:
        src = self.current()
        if src is None:
            return
        self.level.setVisible(src.laravel)
        self.clear_btn.setVisible(src.path is not None)
        level = self.level.currentData() if src.laravel else "debug"
        self.run(lambda: logs.filter_level(logs.read(src, self.ctl.runner, 600 if src.laravel else 300), level),
                 done=self._show)

    def clear(self) -> None:
        src = self.current()
        if src is None or src.path is None or not src.path.exists():
            return
        if self.win.confirm(f"Empty {src.path.name}? This cannot be undone."):
            try:
                logs.clear(src.path)
            except OSError as e:
                self.win.error(e)
            self.load()

    def _show(self, text: str) -> None:
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.view.setPlainText(text or "(empty)")
        if at_end:
            bar.setValue(bar.maximum())
