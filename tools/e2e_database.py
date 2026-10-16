"""Real end-to-end test of the database layer (no root): MySQL 8 or MariaDB as a systemd user unit.

Usage: python tools/e2e_database.py <dir with extracted debs> <mysql|mariadb>
  <dir>/mroot  = dpkg -x of mysql-server-core-8.0, mysql-client-core-8.0 and libevent/libprotobuf-lite
  <dir>/maroot = dpkg -x of mariadb-server-core and mariadb-client-core
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

import os
os.environ.setdefault("PIPANEL_UNIT_PREFIX", "pipanel-e2e")   # never touch the units of a real installation
REPO = Path(__file__).resolve().parent.parent   # repo root, wherever it is checked out
sys.path.insert(0, str(REPO))
S, ENGINE = Path(sys.argv[1]), sys.argv[2]
T = Path("/tmp/dpe2e3"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
from pipanel.core.mysql import MySQL, MySQLError
from pipanel.core.paths import Paths
from pipanel.core.php import PHP
from pipanel.core.runner import Runner
from pipanel.core.services import Services

root = S / ("mroot" if ENGINE == "mysql" else "maroot")


def wrapper(name: str, target: str, extra: str = "") -> str:
    """The packages are extracted into a folder, so point the loader/PATH at it."""
    f = T / name
    f.write_text(f"#!/bin/sh\nexport LD_LIBRARY_PATH={root}/usr/lib/x86_64-linux-gnu\n"
                 f"export PATH={root}/usr/bin:$PATH\nexec {root}/{target} {extra} \"$@\"\n")
    f.chmod(0o755)
    return str(f)


paths = Paths.under(T); paths.ensure()
if ENGINE == "mysql":
    kw = dict(mysqld=wrapper("server", "usr/sbin/mysqld"), client=wrapper("client", "usr/bin/mysql"))
else:
    kw = dict(mysqld=wrapper("server", "usr/sbin/mariadbd"), client=wrapper("client", "usr/bin/mariadb"),
              init=wrapper("install-db", "usr/bin/mariadb-install-db", f"--basedir={root}/usr"))
my = MySQL(paths, Runner(), port=13306, engine=ENGINE, **kw)
my.write_config()
cnf = my.cnf.read_text().replace("basedir=/usr", f"basedir={root}/usr").replace(
    "lc-messages-dir=/usr/share/mysql", f"lc-messages-dir={root}/usr/share/mysql")
my.cnf.write_text(cnf)
my.initialize(); print("initialized:", my.initialized(), "| engine:", my.label)
svc = Services(paths, Runner(), PHP(paths))
svc.write_extra_unit("mysql", **my.unit_spec())
from pipanel.core.services import unit_name
unit = paths.systemd_user / unit_name("mysql")
subprocess.run(["systemctl", "--user", "link", str(unit)], capture_output=True)
subprocess.run(["systemctl", "--user", "daemon-reload"])
try:
    print("start:", svc.start("mysql").ok)
    for _ in range(60):
        if my.ping():
            break
        time.sleep(0.5)
    print("ping:", my.ping(), "| state:", svc.status(["mysql"]))
    my.create_database("app_db"); my.create_database("blog")
    print("databases:", my.databases())
    my.create_user("bob", "p'w\"d\\1", database="app_db"); print("users:", my.users())
    pw = 'r00t "pw" \\ #1'
    my.set_root_password(pw); print("root pw roundtrip:", my.root_password() == pw, "| ping w/ new pw:", my.ping())

    def tcp(user: str, password: str, sql: str):
        r = subprocess.run([kw["client"], "-h127.0.0.1", "-P13306", f"-u{user}", f"-p{password}", "-N", "-e", sql],
                           capture_output=True, text=True)
        return r.stdout.split(), [l for l in r.stderr.splitlines() if "Warning" not in l]
    print("tcp login bob :", tcp("bob", "p'w\"d\\1", "SHOW DATABASES"))
    print("tcp login root:", tcp("root", pw, "SELECT CURRENT_USER()"))
    try:
        my.drop_database("mysql")
    except MySQLError as e:
        print("guard:", e)
    my.drop_database("blog"); my.drop_user("bob"); print("after drops:", my.databases(), my.users())
    print(my.env_snippet("app_db"))
finally:
    svc.stop("mysql")
    subprocess.run(["systemctl", "--user", "disable", unit_name("mysql")], capture_output=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    print("stopped:", svc.status(["mysql"]))
