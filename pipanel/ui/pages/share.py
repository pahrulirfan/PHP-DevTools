from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QComboBox, QLineEdit

from ...core import actions
from ...core.ngrok import SIGNUP_URL, TOKEN_URL
from ..widgets import Card, StatusDot, button, hbox, label
from .base import Page


class SharePage(Page):
    title = "Share"
    icon_name = "share"

    def build(self) -> None:
        self.auth = Card("ngrok account")
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        self.auth_state = label("", wrap=True)
        self.auth.body.addWidget(self.auth_state)
        self.auth.body.addLayout(hbox(self.token, button("Save", None, on_click=self.save_token)))
        self.verify_btn = button("Verify token", "primary", "check", self.verify_token)
        self.get_token = hbox(button("1. Create a free account", "link", on_click=lambda: actions.open_url(SIGNUP_URL)),
                              button("2. Copy your authtoken", "link", on_click=lambda: actions.open_url(TOKEN_URL)),
                              None, self.verify_btn)
        self.auth.body.addLayout(self.get_token)
        self.body.addWidget(self.auth)

        self.share = Card("Share a site")
        self.site = QComboBox()
        self.site.setMinimumWidth(220)
        self.dot = StatusDot()
        self.start = button("Start sharing", "primary", "share", self.start_share)
        self.stop = button("Stop", None, "stop", self.stop_share)
        self.share.body.addLayout(hbox(self.site, self.start, self.stop, None, self.dot))
        self.url = label("", "link")
        self.url.setOpenExternalLinks(True)
        self.copy_btn = button("Copy URL", None, "copy", self.copy)
        self.share.body.addLayout(hbox(self.url, None, self.copy_btn))
        self.share.body.addWidget(label("Free ngrok accounts are limited (one session, and a warning page the first time "
                                        "a link is opened). Sharing always goes through the local HTTP port; ngrok provides the public HTTPS.",
                                        "muted", wrap=True))
        self.body.addWidget(self.share)
        self._timer = QTimer(self, interval=1500)
        self._timer.timeout.connect(self.poll)
        self._tries = 0
        self._verified = False

    def refresh(self) -> None:
        cur = self.site.currentText()
        self.site.clear()
        self.site.addItems([s.name for s in self.ctl.sites()])
        if cur:
            self.site.setCurrentText(cur)
        state = self.ctl.ngrok_state()
        self._verified = state == "verified"
        self.start.setToolTip("" if self._verified else
                              "Save your authtoken and verify it first" if state == "missing" else "Verify your token first")
        self.token.setPlaceholderText("Paste your authtoken here" if state == "missing"
                                      else "Token saved. Paste a new one to replace it")
        self.auth_state.setText({
            "missing": "ngrok needs a free account. Sharing does not work until you save your authtoken: create the "
                       "account, copy the token from the ngrok dashboard, paste it below, and click Save.",
            "unverified": "Token saved, but not verified yet. Click Verify token: ngrok checks that the token works "
                          "(needs internet).",
            "verified": "✓ Token verified. You can share a site.",
        }[state])
        self.auth_state.setProperty("role", "muted" if self._verified else "")
        self.auth_state.style().unpolish(self.auth_state); self.auth_state.style().polish(self.auth_state)
        self.verify_btn.setVisible(state != "missing")
        self.verify_btn.setText("Verify again" if self._verified else "Verify token")
        self.verify_btn.setProperty("variant", None if self._verified else "primary")
        self.verify_btn.style().unpolish(self.verify_btn); self.verify_btn.style().polish(self.verify_btn)
        for i in range(self.get_token.count()):
            w = self.get_token.itemAt(i).widget()
            if w and w is not self.verify_btn:
                w.setVisible(state == "missing")
        self.run(lambda: self.ctl.services.status(["ngrok"]), done=self._status)

    def _status(self, st) -> None:
        up = st.get("ngrok") == "active"
        self.dot.set_state("on" if up else "off")
        self.start.setEnabled(not up and self._verified)
        self.stop.setEnabled(up)
        if up:
            self.poll()
        else:
            self.url.setText("")
            self.copy_btn.setEnabled(False)

    def save_token(self) -> None:
        t = self.token.text()
        if not t:
            return

        def work():
            if not self.ctl.ngrok.installed():
                self.ctl.ngrok.install(self.ctl.fetch)
            return self.ctl.ngrok.set_authtoken(t)

        def done(r):
            self.token.clear()
            if not r.ok:
                self.win.error(r.text)
                return
            self.refresh()
            self.verify_token()   # a saved token is only useful once ngrok accepts it
        self.run(work, done=done, label="Saving authtoken…")

    def verify_token(self) -> None:
        def done(res):
            self.refresh()
            if res.ok:
                self.win.toast("Token verified")
            else:
                self.win.error(f"ngrok did not accept the token:\n\n{res.message}")
        self.run(self.ctl.verify_ngrok, done=done, label="Verifying the ngrok token…")

    def start_share(self) -> None:
        name = self.site.currentText()
        if not name:
            return

        def done(_r):
            self._tries = 0
            self._timer.start()
            self.refresh()
        self.run(lambda: self.ctl.share(name), done=done, label="Starting tunnel…")

    def stop_share(self) -> None:
        self._timer.stop()
        self.run(self.ctl.stop_share, done=lambda _r: self.refresh())

    def poll(self) -> None:
        self._tries += 1
        if self._tries > 20:
            self._timer.stop()
        self.run(self.ctl.share_url, done=self._got_url)

    def _got_url(self, url) -> None:
        if url:
            self._timer.stop()
            self.url.setText(f'<a href="{url}">{url}</a>')
            self.copy_btn.setEnabled(True)
            self._url = url

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(getattr(self, "_url", ""))
        self.win.toast("URL copied")
