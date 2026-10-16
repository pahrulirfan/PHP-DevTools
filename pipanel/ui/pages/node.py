from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout

from ..widgets import _gone, Card, Toggle, badge, button, hbox, label
from .base import Page


class NodePage(Page):
    title = "Node"
    icon_name = "node"

    def build(self) -> None:
        self.fnm = Card("Node.js (fnm)")
        self.fnm_label = label("", "muted", wrap=True)
        self.fnm_btn = button("Install fnm", "primary", on_click=self.install_fnm)
        self.fnm.body.addLayout(hbox(self.fnm_label, None, self.fnm_btn))
        self.shell_toggle = Toggle(False)
        self.shell_toggle.clicked.connect(self.toggle_shell)
        self.fnm.body.addLayout(hbox(label("Shell integration (bash and zsh): switch versions automatically via "
                                           ".node-version / .nvmrc"), None, self.shell_toggle))
        self.body.addWidget(self.fnm)

        self.versions = Card("Installed versions")
        self.list_lay = self.versions.body
        self.body.addWidget(self.versions)

        self.add = Card("Install a new version")
        self.remote = QComboBox()
        self.remote.setEditable(True)
        self.remote.setMinimumWidth(160)
        self.remote.lineEdit().setPlaceholderText("e.g. 22 or lts-latest")
        for v in ("lts-latest", "latest"):
            self.remote.addItem(v)
        self.add.body.addLayout(hbox(self.remote, button("Load list", None, "refresh", self.load_remote),
                                     button("Install", "primary", "plus", self.install), None))
        self.body.addWidget(self.add)

    def refresh(self) -> None:
        ctl = self.ctl
        ok = ctl.node.available()
        self.fnm_label.setText("fnm is installed." if ok else "fnm is not installed. Install it first to manage Node.")
        self.fnm_btn.setVisible(not ok)
        self.shell_toggle.setChecked(ctl.shell.installed())
        self.versions.setEnabled(ok)
        self.add.setEnabled(ok)
        while self.list_lay.count() > 1:
            item = self.list_lay.takeAt(1)
            if item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        _gone(w)
            if item.widget():
                _gone(item.widget())
        if not ok:
            return
        self.run(ctl.node.installed, done=self._show_installed)

    def _show_installed(self, items) -> None:
        while self.list_lay.count() > 1:
            item = self.list_lay.takeAt(1)
            if item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        _gone(w)
            if item.widget():
                _gone(item.widget())
        if not items:
            self.list_lay.addWidget(label("No Node versions yet.", "muted"))
        for v, is_default in items:
            row = QHBoxLayout()
            row.addWidget(label(f"Node {v}", name="cardTitle"))
            if is_default:
                row.addWidget(badge("Default", "primary"))
            row.addStretch(1)
            if not is_default:
                row.addWidget(button("Make default", None, on_click=lambda v=v: self.set_default(v)))
            row.addWidget(button("Remove", "danger", on_click=lambda v=v: self.remove(v)))
            self.list_lay.addLayout(row)

    # ---- actions ----
    def _after(self, r=None) -> None:
        if r is not None and hasattr(r, "ok") and not r.ok:
            self.win.error(r.text)
        self.refresh()

    def install_fnm(self) -> None:
        self.run(self.ctl.install_fnm, label="Downloading fnm…", lock="node", done=lambda _r: self.refresh())

    def toggle_shell(self, on: bool) -> None:
        (self.ctl.shell.install if on else self.ctl.shell.remove)()
        self.win.toast("Open a new terminal for this to take effect")

    def load_remote(self) -> None:
        def done(vs):
            for v in vs:
                if self.remote.findText(v) < 0:
                    self.remote.addItem(v)
        self.run(self.ctl.node.remote, done=done, label="Loading the version list…")

    def install(self) -> None:
        v = self.remote.currentText().strip()
        if v:
            self.run(lambda: self.ctl.node.install(v), label=f"Installing Node {v}…", lock="node", done=self._after)

    def set_default(self, v: str) -> None:
        self.run(lambda: self.ctl.node.set_default(v), done=self._after, label=f"Setting Node {v} as default…", lock="node")

    def remove(self, v: str) -> None:
        self.run(lambda: self.ctl.node.uninstall(v), label=f"Removing Node {v}…", lock="node", done=self._after)
