"""User-owned MySQL 8 or MariaDB: init, config, and database/user management through the command-line client."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import templates as T
from .paths import Paths
from .projects import dotenv_value
from .runner import Result, Runner

_IDENT = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_HOST = re.compile(r"^(localhost|%|[A-Za-z0-9_.:-]{1,60})$")
SYSTEM_DBS = {"information_schema", "mysql", "performance_schema", "sys"}


class MySQLError(Exception):
    pass


@dataclass(frozen=True)
class Engine:
    key: str            # also the name of its data and config folders
    label: str
    server: str         # server binary
    client: str
    init: str | None    # separate init program (MariaDB), or None when the server initializes itself (MySQL)
    packages: tuple[str, ...]
    apparmor_profile: str


ENGINES = {
    "mysql": Engine("mysql", "MySQL 8", "/usr/sbin/mysqld", "mysql", None,
                    ("mysql-server-core-8.0", "mysql-client-core-8.0"), "usr.sbin.mysqld"),
    "mariadb": Engine("mariadb", "MariaDB", "/usr/sbin/mariadbd", "mariadb", "/usr/bin/mariadb-install-db",
                      ("mariadb-server-core", "mariadb-client-core"), "usr.sbin.mariadbd"),
}
# accounts created by the servers themselves; never shown or touched
INTERNAL_USERS = ("mysql.sys", "mysql.session", "mysql.infoschema", "mariadb.sys", "debian-sys-maint", "mysql")


def ident(name: str) -> str:
    if not _IDENT.match(name):
        raise MySQLError(f"invalid name (letters, digits, _ ; max 64): {name!r}")
    return f"`{name}`"


def sql_str(value: str) -> str:
    if "\x00" in value or "\n" in value or "\r" in value:
        raise MySQLError("value must not contain a newline or NUL")
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def host_str(host: str) -> str:
    if not _HOST.match(host):
        raise MySQLError(f"invalid host: {host!r}")
    return sql_str(host)


class MySQL:
    def __init__(self, paths: Paths, runner: Runner, *, port: int = 3306, engine: str = "mysql",
                 mysqld: str | None = None, client: str | None = None, init: str | None = None):
        if engine not in ENGINES:
            raise MySQLError(f"unknown database engine: {engine!r}")
        self.engine = ENGINES[engine]
        self.paths, self.runner, self.port = paths, runner, port
        self.mysqld = mysqld or self.engine.server        # the server binary (mysqld / mariadbd)
        self.client = client or self.engine.client
        self.init_bin = init or self.engine.init
        self.datadir = paths.db_data(engine)
        self.cfgdir = paths.config / engine

    @property
    def label(self) -> str:
        return self.engine.label

    # --- file ---
    @property
    def cnf(self) -> Path:
        return self.cfgdir / "my.cnf"

    @property
    def client_cnf(self) -> Path:
        return self.cfgdir / "client.cnf"

    @property
    def socket(self) -> Path:
        return self.paths.run / "mysql.sock"

    def write_config(self) -> bool:
        text = T.render_mysql_cnf(datadir=self.datadir, socket=self.socket,
                                  pid=self.paths.run / "mysql.pid", log=self.paths.logs / "mysql.log",
                                  port=self.port, engine=self.engine.key)
        self.cnf.parent.mkdir(parents=True, exist_ok=True)
        changed = not self.cnf.exists() or self.cnf.read_text() != text
        if changed:
            self.cnf.write_text(text)
        if not self.client_cnf.exists():
            self._write_client(None)
        return changed

    def _write_client(self, password: str | None) -> None:
        lines = ["[client]", "user=root", f"socket={self.socket}"]
        if password:
            if "\n" in password or "\r" in password:
                raise MySQLError("password must not contain a newline")
            esc = password.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'password="{esc}"')
        self.client_cnf.parent.mkdir(parents=True, exist_ok=True)
        self.client_cnf.touch(mode=0o600)
        self.client_cnf.chmod(0o600)
        self.client_cnf.write_text("\n".join(lines) + "\n")

    # --- initialization ---
    def initialized(self) -> bool:
        return (self.datadir / "mysql").is_dir()

    def initialize(self) -> Result:
        self.write_config()
        self.datadir.mkdir(parents=True, exist_ok=True)
        self.paths.run.mkdir(parents=True, exist_ok=True)
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        if self.init_bin:   # MariaDB: root gets a normal (empty) password instead of unix_socket auth
            cmd = [self.init_bin, f"--defaults-file={self.cnf}", f"--datadir={self.datadir}",
                   "--auth-root-authentication-method=normal", "--skip-test-db"]
        else:
            cmd = [self.mysqld, f"--defaults-file={self.cnf}", "--initialize-insecure"]
        r = self.runner.run(cmd, timeout=300)
        if not r.ok:
            hint = f" (check AppArmor: run 'Install {self.label}' again)" if "denied" in r.text.lower() else ""
            raise MySQLError(f"initialization failed: {r.text}{hint}")
        return r

    def unit_spec(self) -> dict:
        return dict(description=f"PIPanel: {self.label}",
                    exec_start=f"{self.mysqld} --defaults-file={self.cnf}",
                    exec_pre=[f"/bin/mkdir -p {self.paths.run} {self.paths.logs}"])

    # --- SQL ---
    def query(self, sql: str) -> Result:
        return self.runner.run([self.client, f"--defaults-extra-file={self.client_cnf}", "-N", "-B"], input=sql)

    def _exec(self, sql: str) -> str:
        r = self.query(sql)
        if not r.ok:
            raise MySQLError(r.text or "query failed")
        return r.out

    def ping(self) -> bool:
        return self.query("SELECT 1").ok

    def databases(self) -> list[str]:
        return [d for d in self._exec("SHOW DATABASES").split() if d not in SYSTEM_DBS]

    def create_database(self, name: str) -> None:
        self._exec(f"CREATE DATABASE {ident(name)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")

    def drop_database(self, name: str) -> None:
        if name in SYSTEM_DBS:
            raise MySQLError("system databases cannot be dropped")
        self._exec(f"DROP DATABASE {ident(name)}")

    def users(self) -> list[tuple[str, str]]:
        names = ",".join(sql_str(u) for u in INTERNAL_USERS)
        rows = self._exec(f"SELECT user, host FROM mysql.user WHERE user NOT IN ({names})")
        return [tuple(l.split("\t")) for l in rows.splitlines() if "\t" in l]  # type: ignore[misc]

    def create_user(self, user: str, password: str, *, host: str = "localhost",
                    database: str | None = None) -> None:
        if not _IDENT.match(user):
            raise MySQLError(f"invalid user name: {user!r}")
        u = f"{sql_str(user)}@{host_str(host)}"
        sql = f"CREATE USER {u} IDENTIFIED BY {sql_str(password)};"
        if database:
            sql += f" GRANT ALL PRIVILEGES ON {ident(database)}.* TO {u};"
        self._exec(sql)

    def drop_user(self, user: str, host: str = "localhost") -> None:
        if user == "root":
            raise MySQLError("the root user cannot be dropped")
        self._exec(f"DROP USER {sql_str(user)}@{host_str(host)}")

    def drop_extra_root_accounts(self) -> list[str]:
        """MariaDB's installer also creates root@127.0.0.1, root@::1 and root@<hostname> with an empty password,
        and changing the password only affects root@localhost. Keep only root@localhost."""
        hosts = [h for u, h in self.users() if u == "root" and h != "localhost"]
        if hosts:
            self._exec(";".join(f"DROP USER 'root'@{host_str(h)}" for h in hosts))
        return hosts

    def wait_ready(self, timeout: float = 30.0) -> bool:
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.ping():
                return True
            time.sleep(0.5)
        return False

    def set_root_password(self, new: str) -> None:
        self.drop_extra_root_accounts()
        self._exec(f"ALTER USER 'root'@'localhost' IDENTIFIED BY {sql_str(new)}; FLUSH PRIVILEGES;")
        self._write_client(new)

    def root_password(self) -> str:
        try:
            m = re.search(r'^password="(.*)"$', self.client_cnf.read_text(), re.M)
        except OSError:
            return ""
        return m.group(1).replace('\\"', '"').replace("\\\\", "\\") if m else ""

    def env_snippet(self, database: str = "laravel") -> str:
        return (f"DB_CONNECTION=mysql\nDB_HOST=127.0.0.1\nDB_PORT={self.port}\n"
                f"DB_DATABASE={database}\nDB_USERNAME=root\nDB_PASSWORD={dotenv_value(self.root_password())}\n")
