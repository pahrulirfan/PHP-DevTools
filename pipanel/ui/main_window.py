from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
                               QPushButton, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

from ..core.controller import Controller
from . import theme
from .icons import icon, logo_icon
from .pages.laravel import LaravelPage
from .pages.logs import LogsPage
from .pages.mail import MailPage
from .pages.mysql import MySQLPage
from .pages.node import NodePage
from .pages.php import PHPPage
from .pages.services import ServicesPage
from .pages.settings import SettingsPage
from .pages.share import SharePage
from .pages.sites import SitesPage
from ..core.runner import Result
from ..core.services import service_title
from .activity import ActivityPanel
from .notify import Notifier, Watchdog
from .palette import Palette
from .status import SidebarStatus, StatusMonitor
from .tasks import Tasks
from .widgets import Banner, button, clear_layout, hbox, icon_button, label

PAGES = [SitesPage, LaravelPage, PHPPage, NodePage, MySQLPage, MailPage, SharePage, ServicesPage, LogsPage,
         SettingsPage]
POLL_VISIBLE, POLL_HIDDEN = 4000, 15000   # ms between service state checks


class MainWindow(QWidget):
    def __init__(self, ctl: Controller, themer: theme.ThemeManager):
        super().__init__()
        self.ctl, self.themer = ctl, themer
        self.setWindowTitle("PIPanel")
        self.setWindowIcon(logo_icon())
        self.resize(1040, 680)
        self.setMinimumSize(880, 560)
        self.tray_available = False
        self.tasks = Tasks(self)
        self.tasks.error_handler = self.error
        self.monitor = StatusMonitor(ctl, self.tasks, self, POLL_HIDDEN)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- sidebar ---
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 18, 14, 14)
        sl.setSpacing(4)
        head = QHBoxLayout()
        pic = QLabel()
        pic.setPixmap(logo_icon().pixmap(28, 28))
        head.addWidget(pic)
        head.addWidget(label("PIPanel", name="logo"))
        head.addStretch(1)
        sl.addLayout(head)
        sl.addSpacing(14)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.stack = QStackedWidget()
        self.pages: list = []
        self.nav: list[QPushButton] = []
        for i, cls in enumerate(PAGES):
            page = cls(ctl, self.tasks, self)
            self.pages.append(page)
            self.stack.addWidget(page)
            b = QPushButton(page.title)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=i: self.show_page(i))
            self.group.addButton(b)
            self.nav.append(b)
            sl.addWidget(b)
        sl.addStretch(1)
        self.side_status = SidebarStatus(self, self.monitor)
        sl.addWidget(self.side_status)
        root.addWidget(side)

        # --- konten ---
        content = QWidget()
        content.setObjectName("content")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        top = QWidget()
        top.setObjectName("topbar")
        self.top = QHBoxLayout(top)
        self.top.setContentsMargins(32, 22, 24, 12)
        self.top.setSpacing(8)
        self.title = label("", name="pageTitle")
        self.top.addWidget(self.title)
        self.top.addStretch(1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.top.addLayout(self.actions)
        self.theme_btn = icon_button("moon", "Switch theme", self.themer.toggle)
        self.top.addWidget(self.theme_btn)
        cl.addWidget(top)

        self.banner = Banner("First-time setup is not done yet: packages, .test DNS, and certificates need to be "
                             "installed once (needs the admin password).", "Run setup", self.run_setup, "rocket")
        wrap = QHBoxLayout()
        wrap.setContentsMargins(32, 0, 32, 8)
        wrap.addWidget(self.banner)
        cl.addLayout(wrap)

        # a service that stopped working: the reason and a fix, right where the developer looks
        self.problem = Banner("", "Restart", self.restart_problem, "refresh")
        self.problem.details = button("Details", None, on_click=lambda: self.show_problem(self._problem))
        self.problem.layout().addWidget(self.problem.details)
        self.problem.setVisible(False)
        self._problem = ""
        pw = QHBoxLayout()
        pw.setContentsMargins(32, 0, 32, 8)
        pw.addWidget(self.problem)
        cl.addLayout(pw)

        self.activity = ActivityPanel(self.tasks, self.error)
        bw = QHBoxLayout()
        bw.setContentsMargins(32, 0, 32, 8)
        bw.addWidget(self.activity)
        cl.addLayout(bw)

        cl.addWidget(self.stack, 1)
        root.addWidget(content, 1)

        self._toast = QLabel(self)
        self._toast.setObjectName("busy")
        self._toast.setVisible(False)
        self._toast.setStyleSheet("padding: 8px 14px;")
        self._toast_timer = QTimer(self, singleShot=True, interval=3500)
        self._toast_timer.timeout.connect(lambda: self._toast.setVisible(False))

        themer.changed.connect(self._theme_changed)
        self._theme_changed(themer.current)
        self.notifier = Notifier(parent=self)
        self.watchdog = Watchdog(ctl, self.monitor, self, self.notifier)
        self.watchdog.broken.connect(self._broken)
        self.monitor.changed.connect(self._maybe_fixed)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.open_palette)

        self.show_page(0)
        self.update_banner()
        self.monitor.refresh()

    # ---- navigasi ----
    def show_page(self, i: int) -> None:
        page = self.pages[i]
        self.nav[i].setChecked(True)
        self._paint_nav()
        self.stack.setCurrentIndex(i)
        self.title.setText(page.title)
        clear_layout(self.actions)
        for w in page.header_actions():
            self.actions.addWidget(w)

    def go(self, title: str) -> None:
        for i, p in enumerate(self.pages):
            if p.title == title:
                self.show_page(i)

    def page(self, title: str):
        return next(p for p in self.pages if p.title == title)

    def bring_up(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def open_palette(self) -> None:
        self.bring_up()
        Palette(self).exec()

    def show_laravel(self, site: str) -> None:
        self.go("Laravel")
        self.page("Laravel").select(site)

    def show_logs(self, site: str) -> None:
        self.go("Logs")
        self.page("Logs").show_site(site)

    def share_site(self, site: str) -> None:
        self.go("Share")
        self.page("Share").select(site)

    def run_diagnostics(self) -> None:
        self.bring_up()
        self.go("Services")
        self.page("Services").run_doctor()

    # ---- a service that stopped working ----
    def _broken(self, name: str, text: str) -> None:
        self._problem = name
        first = next((l.strip() for l in text.splitlines()[1:] if l.strip()), "")
        self.problem.text.setText(f"{service_title(name, self.ctl.db_label)} stopped working"
                                  + (f": {first}" if first else "."))
        self.problem.text.setToolTip(text)
        self.problem.setVisible(True)

    def _maybe_fixed(self, states: dict) -> None:
        if self._problem and states.get(self._problem) == "active":
            self.problem.setVisible(False)
            self._problem = ""

    def restart_problem(self) -> None:
        name = self._problem
        if name:
            self.watchdog.act(name, "restart")

    def show_problem(self, name: str) -> None:
        """Why it stopped (log lines, who holds the port) and the diagnostics."""
        if not name:
            return
        self.bring_up()
        self.go("Services")
        title = f"{service_title(name, self.ctl.db_label)} stopped working"
        self.tasks.run(lambda: self.ctl.failure_report(name),
                       done=lambda text: self.show_steps(title, [(title, Result(1, "", text))]))
        self.page("Services").run_doctor()

    def _paint_nav(self) -> None:
        t = theme.tokens()
        for b, p in zip(self.nav, self.pages):
            b.setIcon(icon(p.icon_name, t["primary-text"] if b.isChecked() else t["text-muted"], 18))

    def _theme_changed(self, current: str) -> None:
        t = theme.TOKENS[current]
        self._paint_nav()
        self.theme_btn.setIcon(icon("sun" if current == "dark" else "moon", t["text-muted"], 18))
        self.theme_btn.setToolTip("Switch to the light theme" if current == "dark" else "Switch to the dark theme")

    def set_theme(self, mode: str) -> None:
        self.ctl.cfg.theme = mode
        self.ctl.save()
        self.themer.apply(mode)

    # ---- umpan balik ----
    def toast(self, text: str) -> None:
        self._toast.setText(text)
        self._toast.adjustSize()
        self._toast.move(self.width() - self._toast.width() - 24, self.height() - self._toast.height() - 20)
        self._toast.setVisible(True)
        self._toast.raise_()
        self._toast_timer.start()

    def error(self, err) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("PIPanel")
        box.setText(str(err) or "Something went wrong")
        box.exec()

    def confirm(self, text: str) -> bool:
        return QMessageBox.question(self, "PIPanel", text) == QMessageBox.Yes

    def show_steps(self, title: str, steps) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        lay = QVBoxLayout(dlg)
        out = QTextEdit()
        out.setReadOnly(True)
        out.setPlainText("\n\n".join(f"{'✓' if r.ok else '✗'} {name}" + (f"\n{r.text}" if (r.text and not r.ok) else "")
                                     for name, r in steps))
        lay.addWidget(out)
        lay.addLayout(hbox(None, button("Close", "primary", on_click=dlg.accept)))
        dlg.resize(560, 380)
        dlg.exec()

    # ---- setup ----
    def update_banner(self) -> None:
        self.banner.setVisible(not (self.ctl.php.installed() and self.ctl.priv.available()))

    def run_setup(self) -> None:
        def done(steps):
            self.update_banner()
            self.show_steps("First-time setup", steps)
            self.pages[self.stack.currentIndex()].refresh()
        self.tasks.run(self.ctl.setup, done=done, label="First-time setup (follow the password dialog)…", lock="system")

    # ---- tray ----
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.monitor.set_interval(POLL_VISIBLE)
        self.monitor.refresh()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self.monitor.set_interval(POLL_HIDDEN)

    def closeEvent(self, e: QCloseEvent) -> None:
        if self.tray_available and not getattr(self, "quitting", False):
            e.ignore()
            self.hide()
        else:
            e.accept()
