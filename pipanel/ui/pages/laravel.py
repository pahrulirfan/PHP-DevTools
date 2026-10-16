from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QPlainTextEdit

from ...core.laravel import BY_KEY, CATALOG, LaravelError, declared_packages, is_installed, valid_package
from ..widgets import _gone, Card, EmptyState, badge, button, clear_layout, hbox, label
from .base import Page


class LaravelPage(Page):
    title = "Laravel"
    icon_name = "rocket"

    def build(self) -> None:
        self.project = Card("Project")
        self.site = QComboBox()
        self.site.setMinimumWidth(220)
        self.site.setAccessibleName("Laravel project")
        self.site.activated.connect(lambda _i: self.show_packages())
        self.where = label("", "muted")
        self.project.body.addLayout(hbox(self.site, self.where, None,
                                         button("Run migrations", None, "mysql", self.migrate)))
        self.body.addWidget(self.project)

        self.empty = EmptyState("rocket", "No Laravel project yet. Create one from Sites → New Laravel project, "
                                          "or add a folder that has an artisan file.")
        self.body.addWidget(self.empty)

        self.catalog = Card("Packages")
        self.rows_host = self.catalog.body   # rows are rebuilt below the card title
        self.body.addWidget(self.catalog)

        self.custom = Card("Any other package")
        self.pkg = QLineEdit()
        self.pkg.setPlaceholderText("vendor/package  or  vendor/package:^1.0")
        self.dev = QCheckBox("dev dependency")
        self.custom.body.addLayout(hbox(self.pkg, self.dev, button("Install", "primary", "plus", self.install_custom)))
        self.custom.body.addWidget(label("Installed with Composer, using this site's PHP version. Find packages on "
                                         "packagist.org.", "muted", wrap=True))
        self.body.addWidget(self.custom)

        self.log_card = Card("Output")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setProperty("role", "log")
        self.log.setMinimumHeight(160)
        self.log.setPlaceholderText("Composer and artisan output appears here.")
        self.log_card.body.addWidget(self.log)
        self.body.addWidget(self.log_card)

    # ---- state ----
    def refresh(self) -> None:
        sites = self.ctl.laravel_sites()
        cur = self.site.currentText()
        self.site.clear()
        self.site.addItems([s.name for s in sites])
        if cur:
            self.site.setCurrentText(cur)
        have = bool(sites)
        self.empty.setVisible(not have)
        for w in (self.project, self.catalog, self.custom, self.log_card):
            w.setVisible(have)
        if have:
            self.show_packages()

    def current(self):
        name = self.site.currentText()
        return next((s for s in self.ctl.laravel_sites() if s.name == name), None)

    def show_packages(self) -> None:
        site = self.current()
        lay = self.rows_host
        while lay.count() > 1:
            it = lay.takeAt(1)
            if it.widget():
                _gone(it.widget())
            elif it.layout():
                clear_layout(it.layout())
        if site is None:
            return
        self.where.setText(f"PHP {site.php}   ·   {str(site.path).replace(str(__import__('pathlib').Path.home()), '~')}")
        declared = declared_packages(site.path)
        group = None
        for pkg in CATALOG:
            if pkg.group != group:
                group = pkg.group
                lay.addWidget(label(group, "muted"))
            row = QHBoxLayout()
            row.setSpacing(8)
            info = label(pkg.name, name="cardTitle")
            row.addWidget(info)
            if pkg.dev:
                row.addWidget(badge("dev", "muted"))
            row.addWidget(label(pkg.description, "muted"))
            row.addStretch(1)
            if is_installed(pkg, declared):
                row.addWidget(badge("Installed", "success"))
                row.addWidget(button("Remove", None, on_click=lambda p=pkg: self.remove(p)))
            else:
                row.addWidget(button("Install", "primary", "plus", lambda p=pkg: self.install(p)))
            lay.addLayout(row)

    # ---- actions ----
    def _show_steps(self, steps) -> None:
        out = []
        for name, r in steps:
            out.append(f"{'✓' if r.ok else '✗'} {name}")
            text = (r.text or "").strip()
            if text:
                out.append("\n".join("    " + l for l in text.splitlines()[-40:]))
        self.log.setPlainText("\n".join(out))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        failed = [n for n, r in steps if not r.ok]
        self.win.toast("Done" if not failed else f"Failed: {failed[0]}")
        self.show_packages()

    def _do(self, fn, text: str) -> None:
        site = self.current()
        if site is None:
            return
        self.log.setPlainText(f"{text}…")
        self.run(lambda: fn(site), done=self._show_steps, label=f"{text} ({site.name})…", lock=f"composer:{site.path}",
                 failed=lambda e: (self.log.setPlainText(str(e)), self.win.error(e)))

    def migrate(self) -> None:
        self._do(lambda s: [("php artisan migrate --force", self.ctl.laravel.migrate(s.path, s.php))],
                 "Running migrations")

    def install(self, pkg) -> None:
        self._do(lambda s: self.ctl.laravel.install(s.path, s.php, pkg), f"Installing {pkg.name}")

    def remove(self, pkg) -> None:
        if self.win.confirm(f"Remove {pkg.name} from this project?"):
            self._do(lambda s: self.ctl.laravel.remove(s.path, s.php, pkg), f"Removing {pkg.name}")

    def install_custom(self) -> None:
        spec, dev = self.pkg.text().strip(), self.dev.isChecked()
        if not valid_package(spec):
            self.win.error("Enter a package like vendor/package or vendor/package:^1.0")
            return
        self._do(lambda s: self.ctl.laravel.install_custom(s.path, s.php, spec, dev), f"Installing {spec}")
        self.pkg.clear()
