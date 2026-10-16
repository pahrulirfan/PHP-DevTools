from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QInputDialog, QLineEdit, QSizePolicy, QVBoxLayout, QWidget)

from ...core import actions
from ...core.controller import ControllerError
from ...core.sites import LARAVEL, PLAIN, PUBLIC, WORDPRESS, Site, relative_docroot
from ..widgets import Banner, Card, EmptyState, Toggle, badge, button, clear_layout, hbox, icon_button, label
from .base import Page

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
        name = button(f"{site.name}.{ctl.cfg.tld}", "link", on_click=lambda: actions.open_url(ctl.site_url(site)),
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

        for icon_name, tip, fn in (
            ("external", "Open in browser", lambda: actions.open_url(ctl.site_url(site))),
            ("folder", "Open folder", lambda: actions.open_folder(site.path)),
            ("sliders", f"Change web root (now: {relative_docroot(site)})", lambda: page.change_docroot(site)),
            ("code", "Open in VS Code", lambda: actions.open_vscode(site.path)),
            ("terminal", "Open terminal", lambda: actions.open_terminal(site.path)),
        ):
            lay.addWidget(icon_button(icon_name, tip, fn))
        if site.linked:
            lay.addWidget(icon_button("trash", "Remove link", lambda: page.unlink(site)))


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
        self.body.addLayout(hbox(self.parked_label, manage, None))
        self.stopped = Banner("Nginx and DNS are not running, so your .test sites cannot be opened yet.",
                              "Start services", self.start_services, "play")
        self.stopped.setVisible(False)
        self.body.addWidget(self.stopped)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(10)
        self.body.addLayout(self.rows)

    def header_actions(self) -> list[QWidget]:
        return [button("Add projects folder", "primary", "folder-plus", self.park),
                button("Link one folder", None, "folder", self.link),
                button("New Laravel project", None, "rocket", self.new_laravel)]

    def refresh(self) -> None:
        ctl = self.ctl
        clear_layout(self.rows)
        parked = ", ".join(p.replace(str(Path.home()), "~") for p in ctl.cfg.parked)
        self.parked_label.setText(f"Projects folder: {parked}" if parked else "")
        sites = ctl.sites()
        if sites:
            self.run(lambda: ctl.services.status(["nginx", "dns"]),
                     done=lambda st: self.stopped.setVisible(any(v != "active" for v in st.values())),
                     failed=lambda e: None)
        else:
            self.stopped.setVisible(False)
        if not sites:
            self.rows.addWidget(EmptyState("sites", "No sites yet. Choose the folder that holds your projects "
                                           "(for example ~/PHPProject). Every subfolder in it automatically "
                                           "becomes a site: ~/PHPProject/blog is served as blog.test.",
                                           button("Choose projects folder", "primary", "folder-plus", self.park)))
            return
        for s in sites:
            self.rows.addWidget(SiteRow(self, s))

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

    def change_https(self, site: Site, on: bool, toggle: Toggle) -> None:
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
