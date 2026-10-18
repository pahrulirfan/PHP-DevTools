from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QButtonGroup, QFileDialog, QRadioButton, QSpinBox

from ... import __version__
from ...core import actions, autostart
from ...core.controller import ControllerError
from ..widgets import _gone, Card, Toggle, button, hbox, label
from .base import Page

DOCS_URL = "https://pahrulirfan.github.io/PHP-DevTools/"
THEME_LABELS = [("system", "Follow system"), ("light", "Light"), ("dark", "Dark")]


class SettingsPage(Page):
    title = "Settings"
    icon_name = "settings"

    def build(self) -> None:
        ctl = self.ctl
        th = Card("Theme")
        self.group = QButtonGroup(self)
        row = hbox(spacing=20)
        self.radios = {}
        for key, text in THEME_LABELS:
            rb = QRadioButton(text)
            self.group.addButton(rb)
            rb.toggled.connect(lambda on, k=key: on and self.win.set_theme(k))
            self.radios[key] = rb
            row.addWidget(rb)
        row.addStretch(1)
        th.body.addLayout(row)
        self.body.addWidget(th)

        run = Card("In the background")
        self.t_app = Toggle()
        self.t_app.clicked.connect(lambda on: ctl.set_autostart_app(on))
        self.t_svc = Toggle()
        self.t_svc.clicked.connect(self.set_svc)
        run.body.addLayout(hbox(label("Open PIPanel (in the tray) at login"), None, self.t_app))
        self.t_notify = Toggle()
        self.t_notify.clicked.connect(self.set_notify)
        run.body.addLayout(hbox(label("Notify me when a service stops working (with the reason and a fix)"), None,
                                self.t_notify))
        run.body.addLayout(hbox(label("Start the services automatically at login (web server, PHP, and the "
                                      "database, Mailpit and Redis unless you stopped them)"), None, self.t_svc))
        self.body.addWidget(run)

        self.folders = Card("Projects folders")
        self.body.addWidget(self.folders)

        ports = Card("Web ports")
        self.http, self.https = QSpinBox(), QSpinBox()
        for sb in (self.http, self.https):
            sb.setRange(1, 65535)
            sb.setFixedWidth(100)
        self.http.setAccessibleName("HTTP port")
        self.https.setAccessibleName("HTTPS port")
        ports.body.addLayout(hbox(label("HTTP"), self.http, label("HTTPS"), self.https,
                                  button("Apply", "primary", on_click=self.apply_ports), None, spacing=10))
        ports.body.addWidget(label(
            "The defaults are 80 and 443. If another program already uses them (DDEV, Docker, Apache), pick other "
            "ports such as 8080 and 8443; sites then open as http://name.test:8080.", "muted", wrap=True))
        self.body.addWidget(ports)

        sysc = Card("System")
        self.sys_label = label("", "muted", wrap=True)
        sysc.body.addWidget(self.sys_label)
        sysc.body.addLayout(hbox(button("Run first-time setup", "primary", "rocket", self.win.run_setup), None))
        self.body.addWidget(sysc)

        about = Card("About PIPanel")
        about.body.addWidget(label(
            f"PIPanel {__version__}, by Pahrul Irfan, lecturer and researcher in Informatics, Faculty of Engineering, "
            "Universitas Mataram. Much of it was written with the help of Claude (Anthropic's AI assistant, Opus "
            "model). Free and open source; if it saves you time, a donation helps keep it maintained.",
            "muted", wrap=True))
        about.body.addLayout(hbox(button("About", None, "external", lambda: actions.open_url(DOCS_URL + "about.html")),
                                  button("Donate", None, "heart", lambda: actions.open_url(DOCS_URL + "donate.html")),
                                  None))
        self.body.addWidget(about)

    def refresh(self) -> None:
        ctl = self.ctl
        self.radios[ctl.cfg.theme].setChecked(True)
        self.t_app.setChecked(autostart.enabled(ctl.config_home))
        self.t_svc.setChecked(ctl.cfg.autostart_services)
        self.t_notify.setChecked(ctl.cfg.notify)
        self.http.setValue(ctl.cfg.http_port)
        self.https.setValue(ctl.cfg.https_port)
        lay = self.folders.body
        while lay.count() > 1:
            it = lay.takeAt(1)
            if it.widget():
                _gone(it.widget())
            elif it.layout():
                while it.layout().count():
                    w = it.layout().takeAt(0).widget()
                    if w:
                        _gone(w)
        lay.addWidget(label("Every subfolder of a projects folder becomes a site: ~/PHPProject/blog is served as "
                            "blog.test.", "muted", wrap=True))
        if not ctl.cfg.parked:
            lay.addWidget(label("No projects folder yet.", "muted"))
        for p in ctl.cfg.parked:
            lay.addLayout(hbox(label(p.replace(str(Path.home()), "~"), "mono"), None,
                               button("Change…", None, "folder", lambda p=p: self.change_folder(p)),
                               button("Remove", None, on_click=lambda p=p: self.remove_folder(p))))
        lay.addLayout(hbox(button("Add folder", "primary", "folder-plus", self.add_folder), None))
        self.sys_label.setText(f"Domain: *.{ctl.cfg.tld}   DNS: 127.0.0.1:{ctl.cfg.dns_port}\n"
                               f"Config: {str(ctl.paths.config).replace(str(Path.home()), '~')}" +
                               (f"\nRoot helper: {'installed' if ctl.priv.available() else 'not installed (run ./install.sh)'}"
                                if ctl.linux else ""))

    # ---- projects folders ----
    def _pick(self, title: str, start: str) -> str:
        return QFileDialog.getExistingDirectory(self, title, start or str(Path.home()))

    def add_folder(self) -> None:
        d = self._pick("Choose the folder that holds your projects", "")
        if d:
            self._guard(lambda: self.ctl.park(d))

    def change_folder(self, old: str) -> None:
        d = self._pick("Choose the new projects folder", old)
        if d and d != old:
            self._guard(lambda: self.ctl.replace_parked(old, d))

    def remove_folder(self, p: str) -> None:
        self.ctl.unpark(p)
        self.refresh()
        self.run(self.ctl.apply)

    def _guard(self, fn) -> None:
        try:
            fn()
        except ControllerError as e:
            self.win.error(e)
            return
        self.refresh()
        self.run(self.ctl.apply, done=lambda rep: [self.win.toast(w) for w in rep.warnings])

    def apply_ports(self) -> None:
        http, https = self.http.value(), self.https.value()

        def done(rep):
            self.win.toast(f"Web ports set to {http} / {https}")
            for w in rep.warnings:
                self.win.toast(w)
        self.run(lambda: self.ctl.set_ports(http, https), done=done, label="Applying ports…")

    def set_notify(self, on: bool) -> None:
        self.ctl.cfg.notify = on
        self.ctl.save()

    def set_svc(self, on: bool) -> None:
        self.run(lambda: self.ctl.set_autostart_services(on))
