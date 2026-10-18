"""User-owned MySQL 8 or MariaDB: init, config, and database/user management through the command-line client."""
from __future__ import annotations

import gzip
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import templates as T
from .paths import Paths
from .projects import dotenv_value
from .runner import Result, Runner

_IDENT = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_DB_NAME = re.compile(r"^[A-Za-z0-9_$-]{1,64}$")   # database names may contain '-' (quoted with backticks)
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
    dump: str = ""      # mysqldump / mariadb-dump (backups)


ENGINES = {
    "mysql": Engine("mysql", "MySQL 8", "/usr/sbin/mysqld", "mysql", None,
                    ("mysql-server-core-8.0", "mysql-client-core-8.0"), "usr.sbin.mysqld", "/usr/bin/mysqldump"),
    "mariadb": Engine("mariadb", "MariaDB", "/usr/sbin/mariadbd", "mariadb", "/usr/bin/mariadb-install-db",
                      ("mariadb-server-core", "mariadb-client-core", "mariadb-client"), "usr.sbin.mariadbd",
                      "/usr/bin/mariadb-dump"),
}
# accounts created by the servers themselves; never shown or touched
INTERNAL_USERS = ("mysql.sys", "mysql.session", "mysql.infoschema", "mariadb.sys", "debian-sys-maint", "mysql")


def ident(name: str) -> str:
    if not _DB_NAME.match(name):
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
                 mysqld: str | None = None, client: str | None = None, init: str | None = None,
                 dump: str | None = None, basedir: str = "/usr"):
        if engine not in ENGINES:
            raise MySQLError(f"unknown database engine: {engine!r}")
        self.engine = ENGINES[engine]
        self.paths, self.runner, self.port = paths, runner, port
        self.mysqld = mysqld or self.engine.server        # the server binary (mysqld / mariadbd)
        self.client = client or self.engine.client
        self.init_bin = init or self.engine.init
        if dump:
            import dataclasses
            self.engine = dataclasses.replace(self.engine, dump=dump)
        self.basedir = basedir
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
                                  port=self.port, engine=self.engine.key, basedir=self.basedir)
        self.cnf.parent.mkdir(parents=True, exist_ok=True)
        changed = not self.cnf.exists() or self.cnf.read_text() != text
        if changed:
            self.cnf.write_text(text)
        if not self.client_cnf.exists():
            self._write_client(None)
        return changed

    def _write_client(self, password: str | None) -> None:
        from . import platform
        lines = ["[client]", "user=root"]
        if platform.is_windows():   # no unix sockets: TCP to the local server
            lines += ["host=127.0.0.1", f"port={self.port}", "protocol=TCP"]
        else:
            lines.append(f"socket={self.socket}")
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
        from . import platform
        if self.init_bin and platform.is_windows():   # mariadb-install-db.exe has its own, shorter option list
            cmd = [self.init_bin, f"--datadir={self.datadir}", f"--port={self.port}", "--silent"]
        elif self.init_bin:   # MariaDB: root gets a normal (empty) password instead of unix_socket auth
            cmd = [self.init_bin, f"--defaults-file={self.cnf}", f"--datadir={self.datadir}",
                   "--auth-root-authentication-method=normal", "--skip-test-db"]
            if platform.is_macos():
                cmd.insert(2, f"--basedir={self.basedir}")
        else:
            cmd = [self.mysqld, f"--defaults-file={self.cnf}", "--initialize-insecure"]
        r = self.runner.run(cmd, timeout=300)
        if not r.ok:
            hint = f" (check AppArmor: run 'Install {self.label}' again)" if "denied" in r.text.lower() else ""
            raise MySQLError(f"initialization failed: {r.text}{hint}")
        return r

    def unit_spec(self) -> dict:
        return dict(description=f"PIPanel: {self.label}", argv=[str(self.mysqld), f"--defaults-file={self.cnf}"],
                    dirs=[str(self.paths.run), str(self.paths.logs)])

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

    def database_stats(self) -> list[tuple[str, int, int]]:
        """(name, number of tables, size in bytes) of every user database."""
        rows = self._exec(
            "SELECT s.schema_name, COUNT(t.table_name), COALESCE(SUM(t.data_length + t.index_length), 0) "
            "FROM information_schema.schemata s LEFT JOIN information_schema.tables t "
            "ON t.table_schema = s.schema_name GROUP BY s.schema_name ORDER BY s.schema_name")
        out = []
        for line in rows.splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[0] not in SYSTEM_DBS:
                out.append((parts[0], int(parts[1] or 0), int(float(parts[2] or 0))))
        return out

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

    def users_access(self) -> list[tuple[str, str, str]]:
        """(user, host, databases it may use: "*" for all) of every account except the internal ones."""
        names = ",".join(sql_str(u) for u in INTERNAL_USERS)
        rows = self._exec(
            "SELECT u.user, u.host, IF(u.Select_priv = 'Y', '*', COALESCE(GROUP_CONCAT(DISTINCT p.table_schema "
            "ORDER BY p.table_schema SEPARATOR ', '), '')) FROM mysql.user u "
            "LEFT JOIN information_schema.schema_privileges p "
            "ON p.grantee = CONCAT(CHAR(39), u.user, CHAR(39), '@', CHAR(39), u.host, CHAR(39)) "
            f"WHERE u.user NOT IN ({names}) GROUP BY u.user, u.host, u.Select_priv ORDER BY u.user, u.host")
        out = []
        for line in rows.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                out.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
        return out

    def set_user_password(self, user: str, host: str, password: str) -> None:
        if user == "root":
            raise MySQLError("change the root password in the Root password card")
        self._exec(f"ALTER USER {sql_str(user)}@{host_str(host)} IDENTIFIED BY {sql_str(password)}")

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
        hosts = [h for u, h in self.users() if u == "root" and h not in self._root_hosts()]
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

    @staticmethod
    def _root_hosts() -> tuple[str, ...]:
        """Root accounts that PIPanel keeps. On Windows the client connects over TCP, which may arrive as
        root@127.0.0.1 or root@::1 instead of root@localhost."""
        from . import platform
        return ("localhost", "127.0.0.1", "::1") if platform.is_windows() else ("localhost",)

    def set_root_password(self, new: str) -> None:
        self.drop_extra_root_accounts()
        roots = [h for u, h in self.users() if u == "root"] or ["localhost"]
        self._exec(" ".join(f"ALTER USER 'root'@{host_str(h)} IDENTIFIED BY {sql_str(new)};" for h in roots)
                   + " FLUSH PRIVILEGES;")
        self._write_client(new)

    def root_password(self) -> str:
        try:
            m = re.search(r'^password="(.*)"$', self.client_cnf.read_text(), re.M)
        except OSError:
            return ""
        return m.group(1).replace('\\"', '"').replace("\\\\", "\\") if m else ""

    # --- backups ---
    @property
    def backups(self) -> Path:
        return self.paths.data / "backups"

    def default_backup_path(self, database: str) -> Path:
        return self.backups / f"{database}-{time.strftime('%Y%m%d-%H%M%S')}.sql.gz"

    def dump(self, database: str, dest: Path) -> Path:
        """Write a backup of one database: plain SQL, or gzip when `dest` ends in .gz."""
        ident(database)
        if not Path(self.engine.dump).exists():
            raise MySQLError(f"{Path(self.engine.dump).name} is not installed. Services -> Run diagnostics -> Fix "
                             "adds it (run ./install.sh again first if the Doctor says the root helper is outdated).")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        sql = dest.with_name(dest.name[:-3]) if dest.name.endswith(".gz") else dest
        r = self.runner.run([self.engine.dump, f"--defaults-extra-file={self.client_cnf}", "--single-transaction",
                             "--routines", "--triggers", "--events", f"--result-file={sql}", database], timeout=3600)
        if not r.ok:
            sql.unlink(missing_ok=True)
            raise MySQLError(f"backup failed: {r.text}")
        if sql != dest:
            with open(sql, "rb") as src, gzip.open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            sql.unlink()
        return dest

    def import_file(self, database: str, src: Path) -> None:
        """Load a .sql or .sql.gz file into a database (created when it does not exist yet)."""
        ident(database)
        src = Path(src)
        if not src.is_file():
            raise MySQLError(f"file not found: {src}")
        if database not in self.databases():
            self.create_database(database)
        tmp = None
        try:
            if src.name.endswith(".gz"):
                self.backups.mkdir(parents=True, exist_ok=True)   # on disk: /run and /tmp may live in memory
                fd, tmp = tempfile.mkstemp(suffix=".sql", prefix=".import-", dir=self.backups)
                with gzip.open(src, "rb") as zin, open(fd, "wb") as out:
                    shutil.copyfileobj(zin, out)
            r = self.runner.run([self.client, f"--defaults-extra-file={self.client_cnf}", database],
                                stdin_file=tmp or str(src), timeout=3600)
        finally:
            if tmp:
                Path(tmp).unlink(missing_ok=True)
        if not r.ok:
            raise MySQLError(f"import failed: {r.text}")

    def clone(self, source: str, target: str) -> None:
        """Copy a database (for example blog -> blog_test)."""
        ident(source), ident(target)
        if target in self.databases():
            raise MySQLError(f"database {target} already exists")
        self.backups.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".clone-", dir=self.backups)) / f"{source}.sql"
        try:
            self.dump(source, tmp)
            self.import_file(target, tmp)
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)

    def env_snippet(self, database: str = "laravel") -> str:
        return (f"DB_CONNECTION=mysql\nDB_HOST=127.0.0.1\nDB_PORT={self.port}\n"
                f"DB_DATABASE={database}\nDB_USERNAME=root\nDB_PASSWORD={dotenv_value(self.root_password())}\n")
