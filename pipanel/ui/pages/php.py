from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLineEdit, QScrollArea, QFrame,
                               QVBoxLayout, QWidget)

from ...core.config import PHP_DEFAULTS
from ...core.controller import ControllerError
from ...core.php import COMMON_EXTRAS, ESSENTIAL, SUPPORTED
from ..widgets import _gone, Card, Toggle, badge, button, clear_layout, hbox, label
from .base import Page

SETTING_LABELS = {"memory_limit": "memory_limit", "upload_max_filesize": "upload_max_filesize",
                  "post_max_size": "post_max_size", "max_execution_time": "max_execution_time (seconds)"}


class PHPPage(Page):
    title = "PHP"
    icon_name = "php"

    def build(self) -> None:
        self.versions = Card("PHP versions")
        self.body.addWidget(self.versions)

        self.settings = Card("php.ini settings")
        self.fields: dict[str, QLineEdit] = {}
        grid = QGridLayout()
        for i, k in enumerate(PHP_DEFAULTS):
            grid.addWidget(label(SETTING_LABELS[k]), i, 0)
            e = QLineEdit()
            e.setMaximumWidth(160)
            self.fields[k] = e
            grid.addWidget(e, i, 1)
        grid.setColumnStretch(2, 1)
        self.settings.body.addLayout(grid)
        self.settings.body.addLayout(hbox(button("Save", "primary", on_click=self.save_settings), None))
        self.body.addWidget(self.settings)

        self.ext = Card("Extensions")
        self.ext_version = QComboBox()
        self.ext_version.activated.connect(lambda _i: self.refresh_ext())
        self.ext_grid = QGridLayout()
        self.ext_name = QComboBox()
        self.ext_name.setEditable(True)
        self.ext_name.setMinimumWidth(200)
        self.ext_name.addItems(COMMON_EXTRAS)
        self.ext_name.setCurrentIndex(-1)
        self.ext_name.lineEdit().setPlaceholderText("extension name, e.g. redis")
        self.ext.body.addLayout(hbox(label("For PHP", "muted"), self.ext_version, None))
        self.ext.body.addWidget(label(
            "These are the extensions of the installed PHP packages. Like a normal Ubuntu PHP install, every one "
            "of them is enabled by default; untick to disable. Bold names are required by Laravel and Composer.",
            "muted", wrap=True))
        self.ext.body.addLayout(self.ext_grid)
        self.ext.body.addLayout(hbox(self.ext_name, button("Install extension", None, "plus", self.install_ext), None))
        self.body.addWidget(self.ext)

        self.xdebug = Card("Xdebug")
        self.xdebug.body.addWidget(label(
            "Step debugging from your editor (port 9003). Turn it on only while debugging: it slows every request "
            "down. Applies to the sites and the terminal using that PHP version.", "muted", wrap=True))
        self.xdebug_rows = QVBoxLayout()
        self.xdebug_rows.setSpacing(8)
        self.xdebug.body.addLayout(self.xdebug_rows)
        self.launch_site = QComboBox()
        self.launch_site.setMinimumWidth(180)
        self.launch_site.setAccessibleName("Project for launch.json")
        self.xdebug.body.addLayout(hbox(label("VS Code", "muted"), self.launch_site,
                                        button("Create launch.json", None, "code", self.create_launch,
                                               tooltip="Adds a 'Listen for Xdebug' configuration (needs the PHP "
                                                       "Debug extension)"), None))
        self.body.addWidget(self.xdebug)

        self.composer = Card("Composer")
        self.composer_label = label("", "muted")
        self.composer_btn = button("Install Composer", "primary", on_click=self.install_composer)
        self.composer.body.addLayout(hbox(self.composer_label, None, self.composer_btn))
        self.body.addWidget(self.composer)

    def refresh(self) -> None:
        ctl = self.ctl
        installed = ctl.installed_php()
        cli = ctl.php.cli_version()
        lay = self.versions.body
        while lay.count() > 1:
            item = lay.takeAt(1)
            if item.layout():
                clear_layout(item.layout())
            if item.widget():
                _gone(item.widget())
        for v in SUPPORTED:
            row = QHBoxLayout()
            row.addWidget(label(f"PHP {v}", name="cardTitle"))
            if v in installed:
                row.addWidget(badge("Installed", "success"))
            if v == ctl.cfg.default_php:
                row.addWidget(badge("Default", "primary"))
            if v == cli:
                row.addWidget(badge("CLI", "info"))
            if v == "8.1":
                row.addWidget(badge("EOL", "warning"))
            row.addStretch(1)
            if v in installed:
                if v != ctl.cfg.default_php:
                    row.addWidget(button("Make default", None, on_click=lambda v=v: self.set_default(v)))
                    row.addWidget(button("Remove", "danger", on_click=lambda v=v: self.remove(v)))
            else:
                row.addWidget(button("Install", "primary", "plus", lambda v=v: self.install(v)))
            lay.addLayout(row)
        for k, e in self.fields.items():
            e.setText(ctl.cfg.php_settings.get(k, PHP_DEFAULTS[k]))
        cur = self.ext_version.currentData()
        self.ext_version.clear()
        for v in installed:
            self.ext_version.addItem(f"PHP {v}", v)
        if cur in installed:
            self.ext_version.setCurrentIndex(self.ext_version.findData(cur))
        self.refresh_ext()
        self.refresh_xdebug(installed)
        phar = ctl.paths.bin / "composer.phar"
        self.composer_label.setText("Installed at ~/.local/share/pipanel/bin/composer" if phar.exists()
                                    else "Not installed")
        self.composer_btn.setVisible(not phar.exists())

    def refresh_ext(self) -> None:
        clear_layout(self.ext_grid)
        v = self.ext_version.currentData()
        if not v:
            self.ext_grid.addWidget(label("Install a PHP version first.", "muted"), 0, 0)
            return
        for i, (name, on) in enumerate(self.ctl.php.extensions(v)):
            cb = QCheckBox(name)
            cb.setChecked(on)
            if name in ESSENTIAL:
                cb.setProperty("essential", "true")
                cb.setToolTip(f"{name} is required by Laravel and Composer")
            cb.clicked.connect(lambda checked, n=name, v=v: self.toggle_ext(v, n, checked))
            self.ext_grid.addWidget(cb, i // 4, i % 4)

    def refresh_xdebug(self, installed: list[str]) -> None:
        clear_layout(self.xdebug_rows)
        if not installed:
            self.xdebug_rows.addWidget(label("Install a PHP version first.", "muted"))
        for v in installed:
            state = self.ctl.php.xdebug_state(v)
            row = QHBoxLayout()
            row.addWidget(label(f"PHP {v}", name="cardTitle"))
            if state == "missing":
                row.addWidget(label("not installed", "muted"))
                row.addStretch(1)
                row.addWidget(button("Install Xdebug", None, "plus", lambda v=v: self.install_named_ext(v, "xdebug")))
            else:
                row.addWidget(badge("On", "warning") if state == "on" else label("off", "muted"))
                row.addStretch(1)
                tg = Toggle(state == "on")
                tg.setAccessibleName(f"Xdebug for PHP {v}")
                tg.clicked.connect(lambda on, v=v: self.set_xdebug(v, on))
                row.addWidget(tg)
            self.xdebug_rows.addLayout(row)
        cur = self.launch_site.currentText()
        self.launch_site.clear()
        self.launch_site.addItems([s.name for s in self.ctl.sites()])
        if cur:
            self.launch_site.setCurrentText(cur)

    def set_xdebug(self, v: str, on: bool) -> None:
        def done(_r):
            self.win.toast(f"Xdebug {'on' if on else 'off'} for PHP {v}")
            self.refresh()
        self.run(lambda: self.ctl.set_xdebug(v, on), done=done, failed=lambda e: (self.win.error(e), self.refresh()),
                 label=f"Turning Xdebug {'on' if on else 'off'}…")

    def create_launch(self) -> None:
        name = self.launch_site.currentText()
        if name:
            self.run(lambda: self.ctl.vscode_launch(name), label="Writing launch.json…",
                     done=lambda f: self.win.toast(f"Saved .vscode/{f.name}: start 'Listen for Xdebug' in VS Code"))

    # ---- actions ----
    def install(self, v: str) -> None:
        self.run(lambda: self.ctl.install_php(v), label=f"Installing PHP {v}…", lock="system",
                 done=lambda r: self._result(r, f"PHP {v} installed"))

    def remove(self, v: str) -> None:
        self.run(lambda: self.ctl.remove_php(v), label=f"Removing PHP {v}…", lock="system",
                 done=lambda r: self._result(r, f"PHP {v} removed"))

    def _result(self, r, ok_msg: str) -> None:
        self.win.toast(ok_msg) if r.ok else self.win.error(r.text or "failed")
        self.refresh()

    def set_default(self, v: str) -> None:
        self.run(lambda: self.ctl.set_default_php(v), label="Changing the default…", done=lambda _r: self.refresh())

    def save_settings(self) -> None:
        vals = {k: e.text() for k, e in self.fields.items()}
        self.run(lambda: self.ctl.set_php_settings(vals), label="Saving…", done=lambda _r: self.win.toast("Saved"))

    def toggle_ext(self, v: str, name: str, on: bool) -> None:
        if not on and name in ESSENTIAL and not self.win.confirm(
                f"{name} is required by Laravel and Composer. Disable it anyway?"):
            self.refresh_ext()
            return

        def failed(e):
            self.win.error(e)
            self.refresh_ext()
        self.run(lambda: self.ctl.toggle_extension(v, name, on), failed=failed, label="Applying extension…")

    def install_ext(self) -> None:
        v, name = self.ext_version.currentData(), self.ext_name.currentText().strip().lower()
        if v and name:
            self.install_named_ext(v, name)
            self.ext_name.clearEditText()

    def install_named_ext(self, v: str, name: str) -> None:
        self.run(lambda: self.ctl.priv.install_ext(v, name), label=f"Installing {name} for PHP {v}…", lock="system",
                 done=lambda r: self._result(r, f"{name} installed"))

    def install_composer(self) -> None:
        self.run(self.ctl.install_composer, label="Downloading Composer…",
                 done=lambda _p: (self.win.toast("Composer installed"), self.refresh()))
