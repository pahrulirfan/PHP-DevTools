from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout

from ...core.laravel import BY_KEY, CATALOG, LaravelError, declared_packages, is_installed, valid_package
from ...core.services import site_process_service
from ...core import projects
from ..widgets import _gone, Banner, Card, EmptyState, StatusDot, Toggle, badge, button, clear_layout, hbox, label
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
        self.project.body.addLayout(hbox(
            label("Composer", "muted"),
            button("Check for updates", None, "refresh", self.outdated,
                   tooltip="composer outdated --direct: packages with a newer version"),
            button("Security audit", None, "lock", self.audit,
                   tooltip="composer audit: known vulnerabilities in the installed packages"), None))
        self.setup_banner = Banner("", "Set up…", self.setup, "rocket")
        self.setup_banner.setVisible(False)
        self.body.addWidget(self.project)
        self.body.addWidget(self.setup_banner)

        self.procs = Card("Processes")
        self.procs.body.addWidget(label(
            "Run the project's helpers in the background instead of keeping terminals open. They use this site's "
            "PHP and Node versions; their output is on the Logs page.", "muted", wrap=True))
        self.proc_rows = QVBoxLayout()
        self.proc_rows.setSpacing(8)
        self.procs.body.addLayout(self.proc_rows)
        self.proc_widgets: dict[str, tuple[StatusDot, Toggle]] = {}
        self.body.addWidget(self.procs)
        self.win.monitor.changed.connect(self._monitor_changed)

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
        for w in (self.project, self.procs, self.catalog, self.custom, self.log_card):
            w.setVisible(have)
        if not have:
            self.setup_banner.setVisible(False)
        if have:
            self.show_packages()

    def select(self, name: str) -> None:
        self.refresh()
        i = self.site.findText(name)
        if i >= 0:
            self.site.setCurrentIndex(i)
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
        self.show_processes(site)
        needs = projects.setup_needs(site.path) if site is not None else []
        if needs:
            from .setup import needs_text
            self.setup_banner.text.setText(f"{site.name} is not ready to run yet: {needs_text(needs)}.")
        self.setup_banner.setVisible(bool(needs))
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

    def setup(self) -> None:
        site = self.current()
        if site is not None:
            from .setup import run_setup
            run_setup(self, site, done=self.show_packages)

    # ---- processes ----
    def _monitor_changed(self, _states) -> None:
        if self.isVisible():
            self.update_processes()

    def show_processes(self, site) -> None:
        clear_layout(self.proc_rows)
        self.proc_widgets.clear()
        if site is None:
            return
        procs = self.ctl.site_processes(site.name)
        if not procs:
            self.proc_rows.addWidget(label("Nothing to run for this project.", "muted"))
        for proc in procs:
            dot, tg = StatusDot(), Toggle()
            tg.setAccessibleName(f"{proc.title} for {site.name}")
            tg.clicked.connect(lambda on, k=proc.key, t=tg: self.toggle_process(k, on, t))
            row = hbox(dot, label(proc.title, name="cardTitle"), label(proc.detail, "mono"), None, tg, spacing=10)
            self.proc_rows.addLayout(row)
            self.proc_widgets[proc.key] = (dot, tg)
        self.update_processes()

    def update_processes(self) -> None:
        site = self.current()
        if site is None:
            return
        states = self.win.monitor.states
        for key, (dot, tg) in self.proc_widgets.items():
            st = states.get(site_process_service(site.name, key), "inactive")
            dot.set_state({"active": "on", "activating": "on", "failed": "error"}.get(st, "off"))
            tg.setChecked(st in ("active", "activating"))

    def toggle_process(self, key: str, on: bool, toggle: Toggle) -> None:
        site = self.current()
        if site is None:
            return

        def failed(e):
            toggle.setChecked(not on)
            self.win.error(e)
        if on:
            work = lambda: self.ctl.start_process(site.name, key)   # noqa: E731
        else:
            work = lambda: self.ctl.stop_process(site.name, key)    # noqa: E731
        self.run(work, done=lambda _r: self.win.monitor.refresh(), failed=failed,
                 label=f"{'Starting' if on else 'Stopping'} {key} ({site.name})…")

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

    def outdated(self) -> None:
        self._do(lambda s: self.ctl.laravel.outdated(s.path, s.php), "Checking for updates")

    def audit(self) -> None:
        self._do(lambda s: self.ctl.laravel.audit(s.path, s.php), "Running a security audit")

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
