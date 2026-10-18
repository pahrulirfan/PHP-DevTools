"""Tray icon. Its menu lists what is running (with start/stop per service), the PHP versions in use and which
sites use them, and switches the default PHP version."""
from __future__ import annotations

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..core import actions
from ..core.adminer import SITE as ADMINER_SITE
from ..core.controller import Controller
from ..core.mailpit import SITE as MAILPIT_SITE
from ..core.services import MAIL, service_title
from . import theme
from .icons import dot_icon, logo_icon
from .status import UP, StatusMonitor, visible

RECENT_IN_MENU = 8
STATE_TEXT = {"active": "running", "activating": "starting", "failed": "error", "inactive": "stopped",
              "not-found": "not created"}


def php_users(ctl: Controller) -> dict[str, list[str]]:
    """PHP version -> names of the sites that use it."""
    out: dict[str, list[str]] = {}
    for s in ctl.sites():
        out.setdefault(s.php, []).append(s.name)
    return out


def names_hint(names: list[str], limit: int = 2, width: int = 18) -> str:
    """Which sites use a PHP version, short enough for a menu: a couple of names, otherwise only the count
    (a machine can have hundreds of sites)."""
    if len(names) > limit:
        return f"{len(names)} sites"
    return ", ".join(n if len(n) <= width else n[:width - 1] + "…" for n in names)


class Tray:
    def __init__(self, ctl: Controller, win, app: QApplication, monitor: StatusMonitor):
        self.ctl, self.win, self.app, self.monitor = ctl, win, app, monitor
        self.icon = QSystemTrayIcon(logo_icon(), app)
        self.icon.setToolTip("PIPanel")
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.about_to_show)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(lambda r: self.open() if r == QSystemTrayIcon.Trigger else None)
        monitor.changed.connect(self._changed)
        self.rebuild(monitor.states)

    def show(self) -> None:
        self.icon.show()

    def open(self) -> None:
        self.win.showNormal()
        self.win.activateWindow()

    def about_to_show(self) -> None:
        self.rebuild(self.monitor.refresh_now())

    def _changed(self, states: dict[str, str]) -> None:
        if not self.menu.isVisible():   # rebuilding an open menu would close its submenus
            self.rebuild(states)

    # ---- menu ----
    def _act(self, menu: QMenu, text: str, fn=None, enabled: bool = True) -> QAction:
        a = QAction(text, menu)
        a.setEnabled(enabled)
        if fn:
            a.triggered.connect(lambda _=False, f=fn: f())
        menu.addAction(a)
        return a

    def _run(self, fn, label: str) -> None:
        self.win.tasks.run(fn, label=label, done=lambda _r: self.monitor.refresh(),
                           failed=lambda e: (self.win.error(e), self.monitor.refresh()))

    def rebuild(self, states: dict[str, str]) -> None:
        ctl, m, t = self.ctl, self.menu, theme.tokens()
        m.clear()
        self._act(m, "Open PIPanel", self.open)
        self._act(m, "Search sites and actions…  (Ctrl+K)", self.win.open_palette)
        self.add_sites(m)
        m.addSeparator()

        users = php_users(ctl)
        shown = visible(states, ctl.php_in_use())
        up = sum(1 for s in shown.values() if s in UP)
        self.icon.setToolTip(f"PIPanel: {up} of {len(shown)} services running" if shown else "PIPanel")
        self._act(m, f"Services: {up} of {len(shown)} running" if shown else "No services yet", enabled=False)
        colors = {"active": t["success"], "activating": t["success"], "failed": t["danger"]}
        for name, st in shown.items():
            title = service_title(name, ctl.db_label)
            if name.startswith("php") and users.get(name[3:]):
                title += f"  ·  {names_hint(users[name[3:]])}"
            sub = m.addMenu(dot_icon(colors.get(st, t["off"])), f"{title}  ({STATE_TEXT.get(st, st)})")
            if st in UP:
                self._act(sub, "Stop", lambda n=name: self._run(lambda: ctl.stop_service(n), f"Stopping {n}…"))
                self._act(sub, "Restart", lambda n=name: self._run(lambda: ctl.restart_service(n),
                                                                   f"Restarting {n}…"))
            else:
                self._act(sub, "Start", lambda n=name: self._run(lambda: ctl.start_service(n), f"Starting {n}…"))
            if name == MAIL:
                self._act(sub, "Open inbox", lambda: actions.open_url(ctl.host_url(MAILPIT_SITE)))

        installed = ctl.php.installed()
        if installed:
            m.addSeparator()
            default = ctl.cfg.default_php
            pm = m.addMenu(f"Default PHP: {default}")
            group = QActionGroup(pm)
            for v in installed:
                hint = f"  ·  {names_hint(users[v])}" if users.get(v) else ""
                a = self._act(pm, f"PHP {v}{hint}", lambda v=v: self._run(lambda: ctl.set_default_php(v),
                                                                         f"Making PHP {v} the default…"))
                a.setCheckable(True)
                a.setChecked(v == default)
                group.addAction(a)
            pm.addSeparator()
            self._act(pm, "Sites with their own version keep it",
                      enabled=False)

        tools = []
        if ctl.mailpit.installed():
            tools.append(("Open Mailpit inbox", lambda: actions.open_url(ctl.host_url(MAILPIT_SITE))))
        if ctl.adminer.installed():
            tools.append(("Open Adminer", lambda: actions.open_url(ctl.host_url(ADMINER_SITE))))
        if tools:
            m.addSeparator()
            for text, fn in tools:
                self._act(m, text, fn)

        m.addSeparator()
        self._act(m, "Start all services", lambda: self._run(ctl.start_all, "Starting services…"))
        self._act(m, "Stop all services", lambda: self._run(ctl.stop_all, "Stopping services…"))
        m.addSeparator()
        self._act(m, "Quit", self.quit)

    def add_sites(self, m: QMenu) -> None:
        """Recently opened sites with their quick actions. Short on purpose: with hundreds of sites, the search
        (Ctrl+K) is the way to the others."""
        ctl = self.ctl
        sites = ctl.recent_sites(RECENT_IN_MENU)
        if not sites:
            return
        total = len(ctl.sites()) + len(ctl.proxies())
        sm = m.addMenu("Open site")
        for s in sites:
            sub = sm.addMenu(f"{s.name}.{ctl.cfg.tld}")

            def remember(fn, name=s.name):
                return lambda: (ctl.opened(name), fn())
            self._act(sub, "Open in browser", remember(lambda s=s: actions.open_url(ctl.site_url(s))))
            if hasattr(s, "path"):   # a proxy has no folder
                self._act(sub, "Open in VS Code", remember(lambda s=s: actions.open_vscode(s.path)))
                self._act(sub, "Open terminal", remember(lambda s=s: actions.open_terminal(s.path)))
                self._act(sub, "Open folder", remember(lambda s=s: actions.open_folder(s.path)))
        if total > len(sites):
            sm.addSeparator()
            self._act(sm, f"All {total} sites…  (Ctrl+K)", self.win.open_palette)

    def quit(self) -> None:
        self.win.quitting = True
        self.app.quit()
