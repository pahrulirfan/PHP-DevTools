from __future__ import annotations

from PySide6.QtGui import QGuiApplication

from ...core import actions
from ...core.mailpit import SITE, SMTP_PORT, UI_PORT
from ...core.services import MAIL
from ..widgets import Card, StatusDot, button, hbox, label
from .base import Page


class MailPage(Page):
    """Mailpit: every email the local apps send lands in a web inbox instead of a real mailbox."""
    title = "Mail"
    icon_name = "mail"

    def build(self) -> None:
        self.card = Card("Mailpit")
        self.dot = StatusDot()
        self.state = label("", "muted")
        self.install_btn = button("Install Mailpit", "primary", "download", self.install)
        self.start_btn = button("Start", "primary", "play", lambda: self.control("start"))
        self.stop_btn = button("Stop", None, "stop", lambda: self.control("stop"))
        self.inbox_btn = button("Open inbox", None, "external", self.open_inbox)
        self.card.body.addLayout(hbox(self.dot, self.state, None, self.install_btn, self.start_btn, self.stop_btn,
                                      self.inbox_btn))
        self.card.body.addWidget(label(
            "Catches every email your apps send (password resets, verification, notifications) and shows it in "
            "a web inbox. Nothing is delivered to real addresses. When you stop it, it stays off (also at login and with Start all) until you start it again.", "muted", wrap=True))
        self.info = label("", "mono", wrap=True)
        self.card.body.addWidget(self.info)
        self.body.addWidget(self.card)

        self.laravel = Card("Use it in a Laravel project")
        self.laravel.body.addWidget(label(
            f"Sets MAIL_MAILER=smtp, MAIL_HOST=127.0.0.1 and MAIL_PORT={SMTP_PORT} in the project's .env.",
            "muted", wrap=True))
        self.laravel.body.addLayout(hbox(button("Write to a project's .env…", "primary", "plug", self.write),
                                         button("Copy .env", None, "copy", self.copy_env), None))
        self.body.addWidget(self.laravel)

    def refresh(self) -> None:
        have = self.ctl.mailpit.installed()
        self.install_btn.setVisible(not have)
        for w in (self.start_btn, self.stop_btn, self.inbox_btn):
            w.setVisible(have)
        self.info.setText(f"SMTP     127.0.0.1:{SMTP_PORT}\nInbox    {self.ctl.host_url(SITE)}   "
                          f"(also http://127.0.0.1:{UI_PORT})")
        if not have:
            self.dot.set_state("off")
            self.state.setText("Not installed")
            return
        self.run(lambda: self.ctl.services.status([MAIL]), done=self._status)

    def _status(self, st) -> None:
        up = st.get(MAIL) == "active"
        self.dot.set_state("on" if up else "error" if st.get(MAIL) == "failed" else "off")
        self.state.setText("Running" if up else "Stopped")
        self.start_btn.setEnabled(not up)
        self.stop_btn.setEnabled(up)
        self.inbox_btn.setEnabled(up)

    # ---- actions ----
    def install(self) -> None:
        def done(r):
            self.win.toast("Mailpit installed") if r.ok else self.win.error(r.text)
            self.refresh()
            self.win.monitor.refresh()
        self.run(self.ctl.install_mailpit, done=done, label="Downloading Mailpit…")

    def control(self, verb: str) -> None:
        def done(r):
            if not r.ok:
                self.win.error(r.text)
            self.refresh()
            self.win.monitor.refresh()
        self.run(lambda: getattr(self.ctl, f"{verb}_service")(MAIL), done=done, label="Working…")

    def open_inbox(self) -> None:
        actions.open_url(self.ctl.host_url(SITE))

    def write(self) -> None:
        self.write_env("mail", "Send mail to Mailpit")

    def copy_env(self) -> None:
        QGuiApplication.clipboard().setText("".join(f"{k}={v}\n" for k, v in self.ctl.mailpit.env().items()))
        self.win.toast("Copied to the clipboard")
