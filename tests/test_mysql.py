import pytest

from pipanel.core.mysql import MySQL, MySQLError, ident, sql_str
from pipanel.core.runner import Result


@pytest.fixture
def my(paths, runner):
    return MySQL(paths, runner)


def test_config_written(my, paths):
    assert my.write_config()
    t = my.cnf.read_text()
    assert f"datadir={paths.mysql_data}" in t and f"socket={paths.run}/mysql.sock" in t
    assert "bind-address=127.0.0.1" in t and "port=3306" in t and "secure-file-priv=NULL" in t and "skip-name-resolve" not in t
    assert not my.write_config()
    assert oct(my.client_cnf.stat().st_mode & 0o777) == "0o600"


def test_initialize_command_and_failure(my, runner):
    my.initialize()
    assert runner.calls[-1][-1] == "--initialize-insecure" and runner.calls[-1][1].startswith("--defaults-file=")
    runner.on(["/usr/sbin/mysqld"], Result(1, "", "Permission denied"))
    with pytest.raises(MySQLError, match="AppArmor"):
        my.initialize()


@pytest.mark.parametrize("bad", ["a b", "a;drop", "a`b", "", "x" * 65, "a.b"])   # "a-b" is a valid database name
def test_ident_rejects(bad):
    with pytest.raises(MySQLError):
        ident(bad)


def test_sql_str_escaping():
    assert sql_str("a'b\\c") == "'a''b\\\\c'"
    with pytest.raises(MySQLError):
        sql_str("a\nb")


def test_create_and_drop(my, runner):
    my.create_database("app_db")
    assert "CREATE DATABASE `app_db` CHARACTER SET utf8mb4" in runner.last_kw["input"]
    my.create_user("bob", "p'w", database="app_db")
    sql = runner.last_kw["input"]
    assert "CREATE USER 'bob'@'localhost' IDENTIFIED BY 'p''w'" in sql
    assert "GRANT ALL PRIVILEGES ON `app_db`.* TO 'bob'@'localhost'" in sql
    with pytest.raises(MySQLError):
        my.drop_database("mysql")
    with pytest.raises(MySQLError):
        my.drop_user("root")
    with pytest.raises(MySQLError):
        my.create_user("bob'; --", "x")
    with pytest.raises(MySQLError):
        my.create_user("bob", "x", host="a'b")


def test_databases_filters_system(my, runner):
    runner.on(["mysql"], Result(0, "information_schema\nmysql\napp\nsys\nblog\n"))
    assert my.databases() == ["app", "blog"]


def test_users_parse(my, runner):
    runner.on(["mysql"], Result(0, "root\tlocalhost\nbob\t%\n"))
    assert my.users() == [("root", "localhost"), ("bob", "%")]


def test_query_failure_raises(my, runner):
    runner.on(["mysql"], Result(1, "", "ERROR 1045 Access denied"))
    with pytest.raises(MySQLError, match="Access denied"):
        my.databases()


def test_root_password_roundtrip(my, runner):
    my.write_config()
    pw = 'p"a\\ss#1'
    my.set_root_password(pw)
    assert "ALTER USER 'root'@'localhost' IDENTIFIED BY " in runner.last_kw["input"]
    assert my.root_password() == pw
    assert 'DB_PASSWORD="p\\"a\\\\ss#1"' in my.env_snippet("app") and "DB_DATABASE=app" in my.env_snippet("app")
    with pytest.raises(MySQLError):
        my.set_root_password("a\nb")


def test_unit_spec(my):
    s = my.unit_spec()
    assert s["argv"][-1] == f"--defaults-file={my.cnf}"


# ---------- MariaDB engine ----------
@pytest.fixture
def maria(paths, runner):
    return MySQL(paths, runner, engine="mariadb")


def test_engines_have_separate_folders(paths, runner):
    my, ma = MySQL(paths, runner), MySQL(paths, runner, engine="mariadb")
    assert my.datadir != ma.datadir and my.cnf != ma.cnf and my.client_cnf != ma.client_cnf
    assert ma.datadir == paths.data / "mariadb" and ma.cnf == paths.config / "mariadb" / "my.cnf"
    assert (ma.label, my.label) == ("MariaDB", "MySQL 8")


def test_unknown_engine_rejected(paths, runner):
    with pytest.raises(MySQLError, match="unknown database engine"):
        MySQL(paths, runner, engine="postgres")


def test_mariadb_config_has_no_mysql_only_options(maria):
    maria.write_config()
    t = maria.cnf.read_text()
    assert "mysqlx" not in t and "lc-messages-dir" not in t   # MariaDB refuses to start on unknown options
    assert "secure-file-priv" not in t                        # MariaDB rejects the value NULL (found by a real test)
    assert f"datadir={maria.datadir}" in t and "bind-address=127.0.0.1" in t


def test_mariadb_initialize_uses_install_db(maria, runner):
    maria.initialize()
    cmd = runner.calls[-1]
    assert cmd[0] == "/usr/bin/mariadb-install-db" and f"--datadir={maria.datadir}" in cmd
    assert "--auth-root-authentication-method=normal" in cmd and "--initialize-insecure" not in cmd
    assert cmd[1].startswith("--defaults-file=")


def test_mariadb_binaries_and_unit(maria):
    assert maria.mysqld == "/usr/sbin/mariadbd" and maria.client == "mariadb"
    s = maria.unit_spec()
    assert s["argv"][0] == "/usr/sbin/mariadbd" and s["argv"][1].startswith("--defaults-file=") and "MariaDB" in s["description"]


def test_internal_accounts_are_hidden(maria, runner):
    maria.users()
    sql = runner.last_kw["input"]
    for u in ("mariadb.sys", "mysql.sys", "mysql", "debian-sys-maint"):
        assert f"'{u}'" in sql


def test_initialized_checks_own_datadir(paths, runner):
    ma = MySQL(paths, runner, engine="mariadb")
    (paths.data / "mysql" / "mysql").mkdir(parents=True)     # MySQL data exists...
    assert not ma.initialized() and MySQL(paths, runner).initialized()   # ...but it does not count for MariaDB


def test_extra_root_accounts_are_dropped(maria, runner):
    runner.on(["mariadb"], Result(0, "root\t127.0.0.1\nroot\t::1\nroot\tmyhost\nroot\tlocalhost\nbob\t%\n"))
    assert maria.drop_extra_root_accounts() == ["127.0.0.1", "::1", "myhost"]
    sql = runner.last_kw["input"]
    assert "DROP USER 'root'@'127.0.0.1';DROP USER 'root'@'::1';DROP USER 'root'@'myhost'" in sql
    assert "'localhost'" not in sql and "bob" not in sql


def test_nothing_dropped_when_only_localhost_root(my, runner):
    runner.on(["mysql"], Result(0, "root\tlocalhost\n"))
    assert my.drop_extra_root_accounts() == []
    assert "DROP USER" not in (runner.last_kw.get("input") or "")


def test_set_root_password_also_removes_extra_roots(maria, runner):
    maria.write_config()
    runner.on(["mariadb"], Result(0, "root\t127.0.0.1\nroot\tlocalhost\n"))
    maria.set_root_password("new")
    sqls = [i for i in runner.inputs if i]
    assert any("DROP USER 'root'@'127.0.0.1'" in q for q in sqls)
    assert "ALTER USER 'root'@'localhost'" in sqls[-1]       # the drop happens before the password change
