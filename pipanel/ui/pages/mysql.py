from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLineEdit, QVBoxLayout, QWidget

from ...core import actions
from ...core.adminer import SITE as ADMINER_SITE
from ...core.mysql import ENGINES
from ...core.phpmyadmin import SITE as PMA_SITE
from ...core.services import REDIS
from ..table import Column, DataTable, Row, row_actions
from ..widgets import Card, StatusDot, action_menu, button, clear_layout, hbox, label
from .base import Page

def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


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
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter databases")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMaximumWidth(260)
        self.filter.textChanged.connect(lambda text: self.table.set_filter(text))
        self.db_name = QLineEdit()
        self.db_name.setPlaceholderText("new_database")
        self.db_name.setMaximumWidth(220)
        self.db_name.returnPressed.connect(self.create_db)
        self.dbs.body.addLayout(hbox(self.filter, None, self.db_name,
                                     button("Create", "primary", "plus", self.create_db)))
        self.table = DataTable([Column("Database", stretch=True), Column("Tables", numeric=True),
                                Column("Size", numeric=True)], self._db_actions, actions_width=140,
                               empty="No databases yet. Create one above, or set up a project from the Sites page.")
        self.table.setAccessibleName("Databases")
        self.table.activated.connect(self.open_in_tool)
        self.dbs.body.addWidget(self.table)
        self.body.addWidget(self.dbs)

        self.tools = Card("Database tools")
        self.tools.body.addWidget(label("Web database managers at name.test. They open from this computer only and "
                                        "sign in automatically.", "muted", wrap=True))
        self.tool_rows = QVBoxLayout()
        self.tool_rows.setSpacing(8)
        self.tools.body.addLayout(self.tool_rows)
        self.body.addWidget(self.tools)

        self.users = Card("Users")
        self.user_filter = QLineEdit()
        self.user_filter.setPlaceholderText("Filter users")
        self.user_filter.setClearButtonEnabled(True)
        self.user_filter.setMaximumWidth(260)
        self.users.body.addLayout(hbox(self.user_filter, None))
        self.user_table = DataTable([Column("User", stretch=True), Column("Host"), Column("Databases", stretch=True)],
                                    self._user_actions, actions_width=180, empty="No users yet.")
        self.user_table.setAccessibleName("Users")
        self.user_filter.textChanged.connect(self.user_table.set_filter)
        self.users.body.addWidget(self.user_table)
        self.u_name, self.u_pw = QLineEdit(), QLineEdit()
        self.u_name.setPlaceholderText("user")
        self.u_pw.setPlaceholderText("password")
        self.u_pw.setEchoMode(QLineEdit.Password)
        self.u_db = QComboBox()
        self.u_db.setMinimumWidth(160)
        self.users.body.addLayout(hbox(label("New user", "muted"), self.u_name, self.u_pw, label("access to", "muted"),
                                       self.u_db, button("Create user", "primary", "plus", self.create_user)))
        self.body.addWidget(self.users)

        self.root = Card("Root password")
        self.root_pw = QLineEdit()
        self.root_pw.setEchoMode(QLineEdit.Password)
        self.root_pw.setPlaceholderText("new password")
        self.root.body.addLayout(hbox(self.root_pw, button("Change password", None, on_click=self.set_root), None))
        self.body.addWidget(self.root)

        self.redis = Card("Redis")
        self.r_dot = StatusDot()
        self.r_state = label("", "muted")
        self.r_install = button("Install Redis", "primary", "download", self.install_redis)
        self.r_start = button("Start", "primary", "play", lambda: self.redis_control("start"))
        self.r_stop = button("Stop", None, "stop", lambda: self.redis_control("stop"))
        self.redis.body.addLayout(hbox(self.r_dot, self.r_state, None, self.r_install, self.r_start, self.r_stop))
        self.redis.body.addWidget(label(
            "For cache, queues, sessions and Horizon, on 127.0.0.1:6379. Laravel's default client (phpredis) needs "
            "the redis extension: PHP page -> Extensions -> Install extension. When you stop it, it stays off "
            "(also at login and with Start all) until you start it again.", "muted", wrap=True))
        self.redis.body.addLayout(hbox(button("Use in project…", None, "plug",
                                              lambda: self.write_env("redis", "Connect to Redis")), None))
        self.body.addWidget(self.redis)

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
        self.show_tools()
        self.engine_note.setText(
            "MySQL and MariaDB share port "
            f"{ctl.cfg.mysql_port}, so only one runs at a time. Each keeps its own data "
            f"(~/.local/share/pipanel/{ctl.cfg.db_engine}); switching does not convert existing databases. "
            "When you stop it, it stays off (also at login and with Start all) until you start it again.")
        self.info.setText(f"Host 127.0.0.1   Port {ctl.cfg.mysql_port}   User root\n"
                          f"Socket {ctl.mysql.socket}")
        self.run(lambda: ctl.services.status(["mysql"]), done=self._status)
        have_redis = ctl.redis.available() and ctl.services.has_unit(REDIS)
        self.r_install.setVisible(not have_redis)
        self.r_start.setVisible(have_redis)
        self.r_stop.setVisible(have_redis)
        if have_redis:
            self.run(lambda: ctl.services.status([REDIS]), done=self._redis_status)
        else:
            self.r_dot.set_state("off")
            self.r_state.setText("Not installed")

    def _redis_status(self, st) -> None:
        up = st.get(REDIS) == "active"
        self.r_dot.set_state("on" if up else "error" if st.get(REDIS) == "failed" else "off")
        self.r_state.setText("Running on 127.0.0.1:6379" if up else "Stopped")
        self.r_start.setEnabled(not up)
        self.r_stop.setEnabled(up)

    def _status(self, st) -> None:
        up = st.get("mysql") == "active"
        self.dot.set_state("on" if up else "off")
        self.state.setText("Running" if up else "Stopped" if self.ctl.mysql.initialized() else "Not installed")
        self.start_btn.setEnabled(not up)
        self.stop_btn.setEnabled(up)
        if up:
            self.run(lambda: (self.ctl.mysql.database_stats(), self.ctl.mysql.users_access()), done=self._lists,
                     failed=lambda e: self.state.setText(f"Running, but the query failed: {e}"))
        else:
            self.show_databases([])
            self.show_users([])

    def _lists(self, data) -> None:
        stats, users = data
        self.show_databases(stats)
        self.show_users(users)
        dbs = [name for name, _t, _s in stats]
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
        self.run(lambda: getattr(self.ctl, f"{verb}_service")("mysql"), done=lambda r: self._ctl_done(r),
                 label="Working…")

    def _ctl_done(self, r) -> None:
        if not r.ok:
            self.win.error(r.text)
        self.refresh()

    # ---- the database table ----
    def show_databases(self, stats: list[tuple[str, int, int]]) -> None:
        self._tools = self.ctl.db_tools()
        self.table.set_rows([Row(name, [name, str(tables), human_size(size)], [name.lower(), tables, size])
                             for name, tables, size in stats])

    def _db_actions(self, row: Row) -> QWidget:
        return row_actions([action_menu("Actions", lambda name=row.key: self.db_menu(name),
                                        tooltip=f"Actions for {row.key}")])

    def db_menu(self, name: str) -> list:
        items = [("external", f"Open in {title}", lambda t=title: actions.open_url(self.ctl.db_tool_url(t, name)))
                 for title in self._tools]
        if not items:
            items.append(("download", "Install phpMyAdmin or Adminer to browse it…",
                          lambda: self.win.go("Database")))
        return items + [
            None,
            ("plug", "Use in a Laravel project (.env)…", lambda: self.use_in_project(name)),
            ("download", "Back up (.sql.gz)…", lambda: self.backup(name)),
            ("upload", "Import a .sql / .sql.gz file…", lambda: self.import_file(name)),
            ("copy", "Clone to a new database…", lambda: self.clone(name)),
            None,
            ("trash", "Delete…", lambda: self.drop_db(name))]

    def selected_db(self, quiet: bool = False) -> str | None:
        name = self.table.selected()
        if name is None and not quiet:
            self.win.error("Select a database in the list first.")
        return name

    def select_db(self, name: str) -> None:
        self.table.select(name)

    # ---- the users table ----
    def show_users(self, users: list[tuple[str, str, str]]) -> None:
        self.user_table.set_rows([
            Row(f"{u}@{h}", [u, h, "all databases" if dbs == "*" else (dbs or "none")], [u.lower(), h, dbs])
            for u, h, dbs in users])

    def _user_actions(self, row: Row) -> QWidget:
        user, _, host = row.key.rpartition("@")
        if user == "root":
            return row_actions([label("Root password card below", "muted")])
        return row_actions([action_menu("Actions", lambda: [
            ("lock", "Change password…", lambda: self.user_password(user, host)),
            None,
            ("trash", "Delete…", lambda: self.drop_user(user, host))], tooltip=f"Actions for {user}@{host}")])

    def user_password(self, user: str, host: str) -> None:
        pw, ok = QInputDialog.getText(self, "Change password", f"New password for {user}@{host}:",
                                      QLineEdit.Password)
        if ok and pw:
            self._mut(lambda: self.ctl.mysql.set_user_password(user, host, pw), "Changing password…")

    def drop_user(self, user: str, host: str) -> None:
        if self.win.confirm(f"Delete the user {user}@{host}?"):
            self._mut(lambda: self.ctl.mysql.drop_user(user, host), "Deleting user…")

    # ---- database tools ----
    def show_tools(self) -> None:
        clear_layout(self.tool_rows)
        ctl = self.ctl
        for title, text, have, install, site in (
                ("phpMyAdmin", "Full-featured, familiar from shared hosting", ctl.phpmyadmin.installed(),
                 ctl.install_phpmyadmin, PMA_SITE),
                ("Adminer", "Lightweight: a single file", ctl.adminer.installed(), ctl.install_adminer,
                 ADMINER_SITE)):
            row = QHBoxLayout()
            row.addWidget(label(title, name="cardTitle"))
            row.addWidget(label(text, "muted"))
            row.addStretch(1)
            if have:
                row.addWidget(label(f"{site}.{ctl.cfg.tld}", "muted"))
                row.addWidget(button("Open", None, "external",
                                     lambda s=site: actions.open_url(ctl.host_url(s))))
            else:
                row.addWidget(button(f"Install {title}", "primary", "download",
                                     lambda t=title, i=install, s=site: self.install_tool(t, i, s)))
            self.tool_rows.addLayout(row)

    def install_tool(self, title: str, install, site: str) -> None:
        def done(_r):
            self.refresh()
            actions.open_url(self.ctl.host_url(site))
        self.run(install, done=done, label=f"Downloading {title}…")

    def open_in_tool(self, name: str | None) -> None:
        tools = self.ctl.db_tools()
        if name and tools:
            actions.open_url(self.ctl.db_tool_url(next(iter(tools)), name))

    def copy_env(self) -> None:
        db = self.selected_db(quiet=True) or "laravel"
        QGuiApplication.clipboard().setText(self.ctl.mysql.env_snippet(db))
        self.win.toast("Copied to the clipboard")

    def _mut(self, fn, label_: str) -> None:
        self.run(fn, done=lambda _r: self.refresh(), label=label_)

    def create_db(self) -> None:
        n = self.db_name.text().strip()
        if n:
            self._mut(lambda: self.ctl.mysql.create_database(n), "Creating database…")
            self.db_name.clear()

    def drop_db(self, name: str) -> None:
        if self.win.confirm(f"Delete database {name}? This cannot be undone."):
            self._mut(lambda: self.ctl.mysql.drop_database(name), "Deleting database…")

    def create_user(self) -> None:
        u, pw = self.u_name.text().strip(), self.u_pw.text()
        db = self.u_db.currentText() if self.u_db.currentIndex() > 0 else None
        if u and pw:
            self._mut(lambda: self.ctl.mysql.create_user(u, pw, database=db), "Creating user…")
            self.u_name.clear(); self.u_pw.clear()

    # ---- backups ----
    def backup(self, db: str) -> None:
        path, _ = QFileDialog.getSaveFileName(self, f"Back up {db}", str(self.ctl.mysql.default_backup_path(db)),
                                              "SQL (*.sql.gz *.sql)")
        if path:
            self.run(lambda: self.ctl.mysql.dump(db, path), label=f"Backing up {db}…",
                     done=lambda f: self.win.toast(f"Saved {f}"))

    def import_file(self, db: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"Import into {db}", str(self.ctl.mysql.backups),
                                              "SQL (*.sql *.sql.gz)")
        if path and self.win.confirm(f"Load {path} into {db}? Tables in the file replace tables with the same name."):
            self._mut(lambda: self.ctl.mysql.import_file(db, path), f"Importing into {db}…")

    def clone(self, db: str) -> None:
        name, ok = QInputDialog.getText(self, "Clone database", f"Copy {db} to a new database named:",
                                        text=f"{db}_copy")
        if ok and name.strip():
            self._mut(lambda: self.ctl.mysql.clone(db, name.strip()), f"Cloning {db}…")

    def use_in_project(self, db: str) -> None:
        self.write_env("database", f"Use {db}", database=db)

    def install_redis(self) -> None:
        def done(steps):
            if any(not r.ok for _, r in steps):
                self.win.show_steps("Install Redis", steps)
            self.refresh()
            self.win.monitor.refresh()
        self.run(self.ctl.install_redis, done=done, label="Installing Redis…", lock="system")

    def redis_control(self, verb: str) -> None:
        def done(r):
            if not r.ok:
                self.win.error(r.text)
            self.refresh()
            self.win.monitor.refresh()
        self.run(lambda: getattr(self.ctl, f"{verb}_service")(REDIS), done=done, label="Working…")

    def set_root(self) -> None:
        pw = self.root_pw.text()
        if pw:
            self._mut(lambda: self.ctl.mysql.set_root_password(pw), "Changing password…")
            self.root_pw.clear()
