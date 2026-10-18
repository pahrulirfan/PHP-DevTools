from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QInputDialog, QLineEdit, QMenu, QSizePolicy, QVBoxLayout, QWidget)

from ...core import actions, projects
from ...core.controller import ControllerError
from ...core.runner import Result
from ...core.sites import LARAVEL, PLAIN, PUBLIC, WORDPRESS, Proxy, Site, relative_docroot
from ..table import Pager
from ..widgets import Banner, Card, EmptyState, Toggle, action_menu, badge, button, clear_layout, hbox, label
from .base import Page

FILTER_FROM = 6   # show the filter box once there are more sites than this
PAGE_SIZE = 20    # site cards per page
KIND = {LARAVEL: ("Laravel", "primary"), WORDPRESS: ("WordPress", "info"), PUBLIC: ("PHP", "muted"), PLAIN: ("PHP", "muted")}


class SiteRow(Card):
    def __init__(self, page: "SitesPage", site: Site):
        super().__init__()
        self.page, self.site = page, site
        ctl = page.ctl
        lay = QHBoxLayout()
        lay.setSpacing(8)
        self.body.addLayout(lay)

        info = QVBoxLayout()
        info.setSpacing(2)
        name = button(f"{site.name}.{ctl.cfg.tld}", "link", on_click=lambda: page.open(site, actions.open_url,
                                                                                    ctl.site_url(site)),
                      tooltip="Open in browser")
        info.addWidget(name)
        where = str(site.path).replace(str(Path.home()), "~")
        path_label = label(where, "muted")
        path_label.setMinimumWidth(160)
        path_label.setToolTip(f"{site.path}\nweb root: {site.docroot}")
        path_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # long paths shrink instead of overflowing
        info.addWidget(path_label)
        lay.addLayout(info, 1)

        text, kind = KIND.get(site.kind, ("PHP", "muted"))
        needs = projects.setup_needs(site.path) if site.kind == LARAVEL else []
        if needs:   # cloned from Git: no vendor/, no .env, ...
            from .setup import needs_text
            setup = button("Set up", None, "alert", on_click=lambda: page.setup(site),
                           tooltip="Not ready to run: " + needs_text(needs) +
                                   ".\nClick to run composer install, create .env, APP_KEY and the database.")
            setup.setProperty("needsSetup", True)
            setup.setAccessibleName(f"Set up {site.name}")
            lay.addWidget(setup)
        lay.addWidget(badge(text, kind))
        if site.custom_docroot:   # only shown when the web root was changed from the auto-detected one
            lay.addWidget(badge(f"root: {relative_docroot(site)}", "info"))

        combo = QComboBox()
        combo.setAccessibleName("PHP version")
        versions = ctl.installed_php()
        if site.php not in versions:
            versions = sorted(versions + [site.php])
        for v in versions:
            combo.addItem(f"PHP {v}", v)
        combo.setCurrentIndex(max(0, combo.findData(site.php)))
        combo.activated.connect(lambda i, c=combo: page.change_php(site, c.itemData(i)))
        lay.addWidget(combo)

        lay.addWidget(label("HTTPS", "muted"))
        tg = Toggle(site.https)
        tg.setAccessibleName(f"HTTPS for {site.name}")
        tg.clicked.connect(lambda on, t=tg: page.change_https(site, on, t))
        lay.addWidget(tg)

        self.actions = action_menu("Actions", lambda: page.site_actions(site), tooltip=f"Actions for {site.name}")
        self.actions.setAccessibleName(f"Actions for {site.name}")
        lay.addWidget(self.actions)


class ProxyRow(Card):
    """name.test forwarded to another local server (Vite, Next.js, a Go or Python app)."""

    def __init__(self, page: "SitesPage", proxy: Proxy):
        super().__init__()
        self.page, self.proxy = page, proxy
        ctl = page.ctl
        lay = QHBoxLayout()
        lay.setSpacing(8)
        self.body.addLayout(lay)
        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(button(f"{proxy.name}.{ctl.cfg.tld}", "link", tooltip="Open in browser",
                              on_click=lambda: actions.open_url(ctl.site_url(proxy))))
        info.addWidget(label(f"→ {proxy.target}", "muted"))
        lay.addLayout(info, 1)
        lay.addWidget(badge("Proxy", "info"))
        lay.addWidget(label("HTTPS", "muted"))
        tg = Toggle(proxy.https)
        tg.setAccessibleName(f"HTTPS for {proxy.name}")
        tg.clicked.connect(lambda on, t=tg: page.change_https(proxy, on, t))
        lay.addWidget(tg)
        self.actions = action_menu("Actions", lambda: [
            ("external", "Open in browser", lambda: page.open(proxy, actions.open_url, ctl.site_url(proxy))),
            ("share", "Share publicly…", lambda: page.win.share_site(proxy.name)),
            ("logs", "View logs", lambda: page.win.show_logs(proxy.name)),
            None,
            ("trash", "Remove proxy", lambda: page.remove_proxy(proxy))])
        lay.addWidget(self.actions)


class ProxyDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Proxy to a local port")
        self.name = QLineEdit()
        self.name.setPlaceholderText("app")
        self.target = QLineEdit()
        self.target.setPlaceholderText("3000  or  http://127.0.0.1:5173")
        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Forward to", self.target)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(label("Opens another development server (Node, Next.js, Vite, Go, Python, ...) at "
                            "name.test, optionally with HTTPS.", "muted", wrap=True))
        lay.addLayout(form)
        lay.addWidget(bb)
        self.setMinimumWidth(420)


class NewLaravelDialog(QDialog):
    def __init__(self, parent, has_mysql: bool):
        super().__init__(parent)
        self.setWindowTitle("New Laravel project")
        self.name = QLineEdit()
        self.name.setPlaceholderText("project-name")
        self.mysql = QCheckBox("Create a MySQL database and fill in .env")
        self.mysql.setChecked(has_mysql)
        self.mysql.setEnabled(has_mysql)
        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("", self.mysql)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)
        self.setMinimumWidth(380)


class SitesPage(Page):
    title = "Sites"
    icon_name = "sites"

    def build(self) -> None:
        self.parked_label = label("", "muted")
        manage = button("Manage", "link", on_click=lambda: self.win.go("Settings"),
                        tooltip="Add, change, or remove projects folders")
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter sites")
        self.filter.setToolTip("Ctrl+K searches sites and actions from anywhere")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMaximumWidth(300)
        self.filter.textChanged.connect(self.apply_filter)
        self.count_label = label("", "muted")
        self.body.addLayout(hbox(self.parked_label, manage, None, self.count_label, self.filter))
        self.stopped = Banner("Nginx and DNS are not running, so your .test sites cannot be opened yet.",
                              "Start services", self.start_services, "play")
        self.stopped.setVisible(False)
        self.body.addWidget(self.stopped)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(10)
        self.body.addLayout(self.rows)
        self._items: list = []
        self.pager = Pager(PAGE_SIZE)
        self.pager.changed.connect(self.render_rows)
        self.body.addWidget(self.pager)
        # a new project folder shows up by itself (the folder watcher unit writes its nginx config)
        self._fs = QFileSystemWatcher(self)
        self._fs_timer = QTimer(self, singleShot=True, interval=1500)
        self._fs.directoryChanged.connect(lambda _d: self._fs_timer.start())
        self._fs_timer.timeout.connect(lambda: self.isVisible() and self.refresh())

    def header_actions(self) -> list[QWidget]:
        more = button("Add", None, "plus", tooltip="Link one folder or add a proxy")
        menu = QMenu(more)
        for text, fn in (("Link one folder…", self.link), ("Proxy to a local port…", self.add_proxy)):
            a = QAction(text, menu)
            a.triggered.connect(lambda _=False, f=fn: f())
            menu.addAction(a)
        more.setMenu(menu)
        new = button("New project", None, "rocket", tooltip="Create a Laravel or WordPress project")
        new_menu = QMenu(new)
        for text, fn in (("Laravel…", self.new_laravel), ("WordPress…", self.new_wordpress)):
            a = QAction(text, new_menu)
            a.triggered.connect(lambda _=False, f=fn: f())
            new_menu.addAction(a)
        new.setMenu(new_menu)
        return [button("Add projects folder", "primary", "folder-plus", self.park), more, new]

    def refresh(self) -> None:
        ctl = self.ctl
        clear_layout(self.rows)
        watched = set(self._fs.directories())
        wanted = {p for p in ctl.cfg.parked if Path(p).is_dir()}
        if watched - wanted:
            self._fs.removePaths(list(watched - wanted))
        if wanted - watched:
            self._fs.addPaths(sorted(wanted - watched))
        parked = ", ".join(p.replace(str(Path.home()), "~") for p in ctl.cfg.parked)
        self.parked_label.setText(f"Projects folder: {parked}" if parked else "")
        sites = ctl.sites()
        proxies = ctl.proxies()
        if sites or proxies:
            self.run(lambda: ctl.services.status(["nginx", "dns"]),
                     done=lambda st: self.stopped.setVisible(any(v != "active" for v in st.values())),
                     failed=lambda e: None)
        else:
            self.stopped.setVisible(False)
        self._items = [*sites, *proxies]
        self.filter.setVisible(len(self._items) > FILTER_FROM)
        self.count_label.setText(f"{len(self._items)} sites" if len(self._items) > FILTER_FROM else "")
        if not self._items:
            self.pager.set_total(0)
            self.rows.addWidget(EmptyState("sites", "No sites yet. Choose the folder that holds your projects "
                                           "(for example ~/PHPProject). Every subfolder in it automatically "
                                           "becomes a site: ~/PHPProject/blog is served as blog.test.",
                                           button("Choose projects folder", "primary", "folder-plus", self.park)))
            return
        self.render_rows()

    def matching(self) -> list:
        words = self.filter.text().lower().split()
        return [x for x in self._items
                if all(w in f"{x.name} {getattr(x, 'path', '')} {getattr(x, 'target', '')}".lower() for w in words)]

    def apply_filter(self, _text: str = "") -> None:
        self.pager.set_total(len(self.matching()), reset=True)
        self.render_rows()

    def render_rows(self) -> None:
        """Cards for one page of sites only: a few hundred cards would make every refresh slow."""
        clear_layout(self.rows)
        items = self.matching()
        self.pager.set_total(len(items))
        for x in self.pager.window(items):
            self.rows.addWidget(SiteRow(self, x) if isinstance(x, Site) else ProxyRow(self, x))
        if not items:
            self.rows.addWidget(label("No site matches the filter.", "muted"))

    # ---- actions ----
    def start_services(self) -> None:
        def done(r):
            if r.ok:
                self.win.toast("Services started")
            else:
                self.win.error(r.text or "The services did not start")
            self.refresh()
        self.run(self.ctl.start_all, done=done, label="Starting services…")

    def park(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose the folder that holds your projects", str(Path.home()))
        if d:
            self.guard(lambda: self.ctl.park(d))
            self.sync()

    def link(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose the site folder", str(Path.home()))
        if not d:
            return
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Link one folder", "Site name (without .test):", text=Path(d).name.lower())
        if ok and name:
            self.guard(lambda: self.ctl.link(name, d))
            self.sync()

    def change_docroot(self, site: Site) -> None:
        current = relative_docroot(site) if site.custom_docroot else ""
        text, ok = QInputDialog.getText(
            self, "Web root",
            f"Folder inside {site.path.name} that Nginx serves, for example public, web, or . (the project "
            f"folder itself).\nLeave empty to auto-detect.", text=current)
        if ok:
            self.run(lambda: self.ctl.set_site_docroot(site.name, text), done=self._synced,
                     label="Applying config…")

    def open(self, site, fn, arg) -> None:
        self.ctl.opened(site.name)   # the tray menu lists recently opened sites
        fn(arg)

    def site_actions(self, site: Site) -> list:
        ctl, win = self.ctl, self.win
        items = [("external", "Open in browser", lambda: self.open(site, actions.open_url, ctl.site_url(site))),
                 ("code", "Open in VS Code", lambda: self.open(site, actions.open_vscode, site.path)),
                 ("terminal", "Open terminal", lambda: self.open(site, actions.open_terminal, site.path)),
                 ("folder", "Open folder", lambda: self.open(site, actions.open_folder, site.path)),
                 None]
        if site.kind == LARAVEL:
            items.append(("rocket", "Laravel: packages and processes", lambda: win.show_laravel(site.name)))
            if projects.setup_needs(site.path):
                items.append(("alert", "Set up the project…", lambda: self.setup(site)))
        items += [("logs", "View logs", lambda: win.show_logs(site.name)),
                  ("share", "Share publicly…", lambda: win.share_site(site.name)),
                  None,
                  ("sliders", f"Change web root (now: {relative_docroot(site)})…", lambda: self.change_docroot(site))]
        if site.linked:
            items.append(("trash", "Remove link", lambda: self.unlink(site)))
        return items

    def setup(self, site: Site) -> None:
        from .setup import run_setup
        run_setup(self, site, done=self.refresh)

    def add_proxy(self) -> None:
        dlg = ProxyDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        name, target = dlg.name.text(), dlg.target.text()
        self.run(lambda: self.ctl.add_proxy(name, target), done=self._synced, label="Adding proxy…")

    def remove_proxy(self, proxy: Proxy) -> None:
        if self.win.confirm(f"Remove the proxy {proxy.name}.{self.ctl.cfg.tld}?"):
            self.run(lambda: self.ctl.remove_proxy(proxy.name), done=self._synced, label="Removing proxy…")

    def unlink(self, site: Site) -> None:
        self.ctl.unlink(site.name)
        self.sync()

    def guard(self, fn) -> bool:
        try:
            fn()
            return True
        except ControllerError as e:
            self.win.error(e)
            return False

    def sync(self) -> None:
        self.refresh()
        self.run(self.ctl.apply, done=lambda rep: self._synced(rep), label="Applying config…")

    def _synced(self, rep) -> None:
        for w in rep.warnings:
            self.win.toast(w)
        self.refresh()

    def change_php(self, site: Site, v: str) -> None:
        self.run(lambda: self.ctl.set_site_php(site.name, v), done=self._synced, label="Changing PHP version…")

    def change_https(self, site: Site | Proxy, on: bool, toggle: Toggle) -> None:
        def failed(e):
            toggle.setChecked(not on)
            self.win.error(e)
        self.run(lambda: self.ctl.set_https(site.name, on), done=self._synced, failed=failed,
                 label="Creating certificate…" if on else "Applying config…")

    def new_laravel(self) -> None:
        dlg = NewLaravelDialog(self, self.ctl.mysql.initialized())
        if dlg.exec() != QDialog.Accepted or not dlg.name.text().strip():
            return
        name, mysql = dlg.name.text().strip(), dlg.mysql.isChecked()

        def done(path):
            self.win.toast(f"Project created at {path}")
            for note in self.ctl.last_notes:
                self.win.error(note)
            self.sync()
        self.run(lambda: self.ctl.new_laravel(name, with_mysql=mysql), done=done, label="Creating Laravel project…")

    def new_wordpress(self) -> None:
        name, ok = QInputDialog.getText(self, "New WordPress site",
                                        f"Project name (the site is served as name.{self.ctl.cfg.tld}; a database "
                                        f"with the same name is created):")
        name = name.strip()
        if not ok or not name:
            return

        def done(site):
            self.sync()
            self.win.show_steps("WordPress is ready", [
                (f"{site.url}/wp-admin   user: {site.admin_user}   password: {site.admin_password}", Result(0)),
                (f"Project folder: {site.path}   database: {site.database}", Result(0))])
        self.run(lambda: self.ctl.new_wordpress(name), done=done, label=f"Creating WordPress site {name}…")
