from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QComboBox, QLineEdit, QListWidget

from ...core.mysql import ENGINES
from ..widgets import Card, StatusDot, button, hbox, label
from .base import Page


class MySQLPage(Page):
    """The "Database" page: MySQL 8 or MariaDB (one at a time; they share the port)."""
    title = "Database"
    icon_name = "mysql"

    def build(self) -> None:
        self.status = Card("Database server")
        self.engine = QComboBox()
        self.engine.setAccessibleName("Database engine")
        for key, e in ENGINES.items():
            self.engine.addItem(e.label, key)
        self.engine.activated.connect(lambda i: self.switch_engine(self.engine.itemData(i)))
        self.dot = StatusDot()
        self.state = label("", "muted")
        self.install_btn = button("Install", "primary", on_click=self.install)
        self.start_btn = button("Start", "primary", "play", lambda: self.control("start"))
        self.stop_btn = button("Stop", None, "stop", lambda: self.control("stop"))
        self.status.body.addLayout(hbox(self.engine, self.dot, self.state, None, self.install_btn, self.start_btn,
                                        self.stop_btn))
        self.engine_note = label("", "muted", wrap=True)
        self.status.body.addWidget(self.engine_note)
        self.info = label("", "mono", wrap=True)
        self.status.body.addWidget(self.info)
        self.status.body.addLayout(hbox(button("Copy Laravel .env", None, "copy", self.copy_env), None))
        self.body.addWidget(self.status)

        self.dbs = Card("Databases")
        self.db_list = QListWidget()
        self.db_list.setMinimumHeight(120)
        self.db_name = QLineEdit()
        self.db_name.setPlaceholderText("database_name")
        self.dbs.body.addWidget(self.db_list)
        self.dbs.body.addLayout(hbox(self.db_name, button("Create", "primary", "plus", self.create_db), None,
                                     button("Delete selected", "danger", "trash", self.drop_db)))
        self.body.addWidget(self.dbs)

        self.users = Card("Users")
        self.user_list = QListWidget()
        self.user_list.setMaximumHeight(110)
        self.u_name, self.u_pw = QLineEdit(), QLineEdit()
        self.u_name.setPlaceholderText("user")
        self.u_pw.setPlaceholderText("password")
        self.u_pw.setEchoMode(QLineEdit.Password)
        self.u_db = QComboBox()
        self.users.body.addWidget(self.user_list)
        self.users.body.addLayout(hbox(self.u_name, self.u_pw, label("access to", "muted"), self.u_db,
                                       button("Create user", "primary", on_click=self.create_user)))
        self.body.addWidget(self.users)

        self.root = Card("Root password")
        self.root_pw = QLineEdit()
        self.root_pw.setEchoMode(QLineEdit.Password)
        self.root_pw.setPlaceholderText("new password")
        self.root.body.addLayout(hbox(self.root_pw, button("Change password", None, on_click=self.set_root), None))
        self.body.addWidget(self.root)

    def refresh(self) -> None:
        ctl = self.ctl
        label_ = ctl.db_label
        have = ctl.mysql_available()
        ready = ctl.mysql.initialized()
        self.engine.setCurrentIndex(self.engine.findData(ctl.cfg.db_engine))
        self.install_btn.setVisible(not (have and ready))
        self.start_btn.setVisible(have and ready)
        self.stop_btn.setVisible(have and ready)
        self.install_btn.setText(f"Install {label_}" if not have else "Initialize data")
        for c in (self.dbs, self.users, self.root):
            c.setEnabled(have and ready)
        self.engine_note.setText(
            "MySQL and MariaDB share port "
            f"{ctl.cfg.mysql_port}, so only one runs at a time. Each keeps its own data "
            f"(~/.local/share/pipanel/{ctl.cfg.db_engine}); switching does not convert existing databases.")
        self.info.setText(f"Host 127.0.0.1   Port {ctl.cfg.mysql_port}   User root\n"
                          f"Socket {ctl.mysql.socket}")
        self.run(lambda: ctl.services.status(["mysql"]), done=self._status)

    def _status(self, st) -> None:
        up = st.get("mysql") == "active"
        self.dot.set_state("on" if up else "off")
        self.state.setText("Running" if up else "Stopped" if self.ctl.mysql.initialized() else "Not installed")
        self.start_btn.setEnabled(not up)
        self.stop_btn.setEnabled(up)
        if up:
            self.run(lambda: (self.ctl.mysql.databases(), self.ctl.mysql.users()), done=self._lists,
                     failed=lambda e: self.state.setText(f"Running, but the query failed: {e}"))
        else:
            self.db_list.clear()
            self.user_list.clear()

    def _lists(self, data) -> None:
        dbs, users = data
        self.db_list.clear()
        self.db_list.addItems(dbs)
        self.user_list.clear()
        self.user_list.addItems([f"{u}@{h}" for u, h in users])
        self.u_db.clear()
        self.u_db.addItems(["(all databases)"] + dbs)

    # ---- actions ----
    def switch_engine(self, engine: str) -> None:
        if engine == self.ctl.cfg.db_engine:
            return

        def done(steps):
            if any(not r.ok for _, r in steps):
                self.win.show_steps("Switch database engine", steps)
            self.refresh()
        self.run(lambda: self.ctl.set_db_engine(engine), done=done, label="Switching database engine…",
                 failed=lambda e: (self.win.error(e), self.refresh()))

    def install(self) -> None:
        def done(steps):
            self.win.show_steps(f"Install {self.ctl.db_label}", steps)
            self.refresh()
        self.run(self.ctl.install_database, done=done, label=f"Installing {self.ctl.db_label}…", lock="system")

    def control(self, verb: str) -> None:
        self.run(lambda: getattr(self.ctl.services, verb)("mysql"), done=lambda r: self._ctl_done(r),
                 label="Working…")

    def _ctl_done(self, r) -> None:
        if not r.ok:
            self.win.error(r.text)
        self.refresh()

    def copy_env(self) -> None:
        db = self.db_list.currentItem().text() if self.db_list.currentItem() else "laravel"
        QGuiApplication.clipboard().setText(self.ctl.mysql.env_snippet(db))
        self.win.toast("Copied to the clipboard")

    def _mut(self, fn, label_: str) -> None:
        self.run(fn, done=lambda _r: self.refresh(), label=label_)

    def create_db(self) -> None:
        n = self.db_name.text().strip()
        if n:
            self._mut(lambda: self.ctl.mysql.create_database(n), "Creating database…")
            self.db_name.clear()

    def drop_db(self) -> None:
        it = self.db_list.currentItem()
        if it and self.win.confirm(f"Delete database {it.text()}? This cannot be undone."):
            self._mut(lambda: self.ctl.mysql.drop_database(it.text()), "Deleting database…")

    def create_user(self) -> None:
        u, pw = self.u_name.text().strip(), self.u_pw.text()
        db = self.u_db.currentText() if self.u_db.currentIndex() > 0 else None
        if u and pw:
            self._mut(lambda: self.ctl.mysql.create_user(u, pw, database=db), "Creating user…")
            self.u_name.clear(); self.u_pw.clear()

    def set_root(self) -> None:
        pw = self.root_pw.text()
        if pw:
            self._mut(lambda: self.ctl.mysql.set_root_password(pw), "Changing password…")
            self.root_pw.clear()
