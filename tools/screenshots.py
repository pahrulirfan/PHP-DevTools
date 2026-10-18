"""Render every page of the UI offscreen, in light and dark, with realistic sample data.

    python tools/screenshots.py            # writes docs/assets/img/{light,dark}-<page>.png

No real service is touched: a fake runner answers the system commands, and everything lives in a temp HOME so that
paths show up as ~/PHPProject/... exactly as they would on a real machine.
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parent.parent   # repo root, wherever it is checked out
sys.path.insert(0, str(REPO))

HOME = Path(tempfile.mkdtemp()) / "home"
HOME.mkdir(parents=True)
os.environ["HOME"] = str(HOME)                    # Path.home() -> "~" in the UI
os.environ.pop("XDG_CONFIG_HOME", None)

from PySide6.QtWidgets import QApplication  # noqa: E402

from pipanel.core.controller import Controller  # noqa: E402
from pipanel.core.doctor import Check  # noqa: E402
from pipanel.core.ngrok import fingerprint  # noqa: E402
from pipanel.core.paths import Paths  # noqa: E402
from pipanel.core.php import PHP  # noqa: E402
from pipanel.core.privileged import Privileged  # noqa: E402
from pipanel.core.runner import Result, Runner  # noqa: E402


class Fake(Runner):
    """Answers like a healthy machine: every service is up, the database has a few schemas, fnm has Node versions."""

    def run(self, argv, **kw):
        a = [str(x) for x in argv]
        if a[:3] == ["systemctl", "--user", "is-active"]:
            return Result(0, "\n".join("inactive" if ("ngrok" in u or "cloudflared" in u or "queue" in u
                                                     or "schedule" in u) else "active" for u in a[3:]) + "\n")
        sql = kw.get("input") or ""
        if "information_schema.schemata" in sql:
            return Result(0, "blog\t24\t3407872\nmysql\t31\t2600000\nnews\t12\t1572864\nportfolio\t7\t262144\n"
                             "shop_api\t41\t18874368\n")
        if "SHOW DATABASES" in sql:
            return Result(0, "information_schema\nmysql\nperformance_schema\nblog\nshop_api\nportfolio\nnews\n")
        if "schema_privileges" in sql:
            return Result(0, "blog\tlocalhost\tblog\nroot\tlocalhost\t*\nshop\tlocalhost\tshop_api\n")
        if "FROM mysql.user" in sql:
            return Result(0, "root\tlocalhost\nblog\tlocalhost\nshop\tlocalhost\n")
        if sql.strip().startswith("SELECT 1"):
            return Result(0, "1\n")
        if a[0].endswith("fnm") and a[1:2] == ["list"]:
            return Result(0, "* v20.18.1\n* v22.12.0 default\n* v24.1.0\n")
        return Result(0, "")


# ---------- a believable home folder ----------
proj = HOME / "PHPProject"
sites = (("blog", "laravel"), ("shop-api", "laravel"), ("portfolio", "php"), ("news", "wordpress"), ("legacy", "php"))
for name, kind in sites:
    d = proj / name
    d.mkdir(parents=True)
    if kind == "laravel":
        (d / "artisan").write_text(""); (d / "public").mkdir(); (d / "public/index.php").write_text("")
    elif kind == "wordpress":
        (d / "wp-config.php").write_text("")
    else:
        (d / "index.php").write_text("")
(proj / "legacy" / "web").mkdir()
for name in ("blog", "client-portal"):   # set up already; shop-api is fresh from Git and shows "Set up"
    root = (HOME / "Work" / name) if name == "client-portal" else proj / name
    (root / "vendor").mkdir(parents=True, exist_ok=True); (root / "vendor" / "autoload.php").write_text("")
    (root / ".env").write_text("APP_KEY=base64:sample\n")
(proj / "shop-api" / "composer.json").write_text("{}")
(proj / "blog" / "package.json").write_text('{"scripts": {"dev": "vite"}}')
(proj / "blog" / "composer.json").write_text(
    '{"require": {"laravel/framework": "^12", "livewire/livewire": "^3"}, "require-dev": {"laravel/pint": "^1"}}')
other = HOME / "Work" / "client-portal"
(other / "public").mkdir(parents=True); (other / "artisan").write_text(""); (other / "public/index.php").write_text("")

paths = Paths(config=HOME / ".config/pipanel", data=HOME / ".local/share/pipanel", state=HOME / ".local/state/pipanel",
              run=HOME / ".cache/pipanel-run", systemd_user=HOME / ".config/systemd/user")
paths.ensure()
sbin = HOME / "sbin"; sbin.mkdir()
for v in ("8.2", "8.3", "8.4"):
    (sbin / f"php-fpm{v}").write_text("")
mods = HOME / "etc" / "8.4" / "mods-available"; fpm = HOME / "etc" / "8.4" / "fpm" / "conf.d"
mods.mkdir(parents=True); fpm.mkdir(parents=True)
EXTS = ("bcmath calendar ctype curl dom exif ffi fileinfo ftp gd gettext iconv intl mbstring mysqli mysqlnd opcache pdo "
        "pdo_mysql pdo_sqlite phar posix readline shmop simplexml sockets sqlite3 sysvmsg sysvsem sysvshm tokenizer "
        "xml xmlreader xmlwriter xsl zip").split()
for e in EXTS:   # Ubuntu enables every extension of an installed package: mirror that
    (mods / f"{e}.ini").write_text("; priority=20\n")
    (fpm / f"20-{e}.ini").symlink_to(mods / f"{e}.ini")
php = PHP(paths, etc_php=HOME / "etc", sbin=sbin, usr_bin=HOME / "bin")
helper = HOME / "helper"; helper.write_text("")
fake = Fake()
ctl = Controller(paths, fake, home=HOME, php=php, privileged=Privileged(fake, helper))
ctl.services._nginx_version = "1.24.0"
ctl.cfg.default_php = "8.4"
ctl.park(proj)
ctl.link("client-portal", other)
Site = type(ctl.cfg.site("blog"))
ctl.cfg.sites["blog"] = Site(php="8.4", https=True)
ctl.cfg.sites["shop-api"] = Site(php="8.3")
ctl.cfg.sites["legacy"] = Site(php="8.2", docroot="web")
ctl.cfg.sites["client-portal"] = Site(php="8.3", https=True)
ctl.save()
php.ensure_conf_d("8.4")
php.write_cli_wrapper("8.4")
(paths.bin / "composer.phar").write_text("")
(paths.bin / "fnm").write_text(""); ctl.shell.install()
(paths.systemd_user / "pipanel-mysql.service").write_text("")
ctl.mysql.mysqld = "/bin/true"; (ctl.mysql.datadir / "mysql").mkdir(parents=True)
type(ctl.mysql).socket = property(lambda self: Path("/run/user/1000/pipanel/mysql.sock"))   # the real location
(paths.bin / "mailpit").write_text("")                                                         # Mailpit installed
(paths.systemd_user / "pipanel-mail.service").write_text("")
(paths.systemd_user / "pipanel-site-blog-vite.service").write_text("")                         # Vite running for blog
(paths.data / "phpmyadmin").mkdir(parents=True); (paths.data / "phpmyadmin" / "index.php").write_text("")
ctl.cfg.proxies["next-shop"] = "http://127.0.0.1:3000"
ctl.cfg.recent_sites = ["blog", "shop-api"]
(paths.bin / "ngrok").write_text("")                                                           # a verified ngrok token
cfg_file = ctl.ngrok.config_files[0]; cfg_file.parent.mkdir(parents=True)
cfg_file.write_text('version: "3"\nagent:\n    authtoken: 2abcDEFghi_1234567890sampleTokenForScreenshots\n')
ctl.cfg.ngrok_verified = fingerprint(ctl.ngrok.read_token()); ctl.save()
LOG = [('GET /', 200, 4821), ('GET /build/assets/app-4a1c.css', 200, 18344), ('GET /build/assets/app-9f20.js', 200, 90211),
       ('POST /login', 302, 0), ('GET /dashboard', 200, 6120), ('GET /posts?page=2', 200, 7312), ('GET /favicon.ico', 404, 146),
       ('POST /posts', 302, 0), ('GET /posts/12/edit', 200, 5530), ('GET /api/notifications', 200, 212)]
paths.logs.mkdir(parents=True, exist_ok=True)
(paths.logs / "blog-access.log").write_text("".join(
    f'127.0.0.1 - - [16/Oct/2026:19:3{i % 6}:{10 + i:02d} +0800] "{req} HTTP/1.1" {code} {size}\n'
    for i in range(3) for req, code, size in LOG))

app = QApplication([])
from pipanel.app import create  # noqa: E402

app, win, tray = create(ctl, app)
OUT = Path(os.environ.get("PIPANEL_SHOTS_OUT") or REPO / "docs" / "assets" / "img")
OUT.mkdir(parents=True, exist_ok=True)
for old in OUT.glob("*.png"):
    old.unlink()
win.resize(1120, 720)
win.show()


def settle(rounds=30):
    for _ in range(rounds):
        win.tasks.wait(40); app.processEvents(); time.sleep(0.01)
    for _ in range(3):
        app.processEvents(); time.sleep(0.04)


def shot(name):
    settle(4)
    win.grab().save(str(OUT / f"{MODE}-{name}.png"))


DOCTOR = [Check("helper", "Root helper installed", "ok"), Check("packages", "Base packages installed", "ok"),
          Check("sysctl", "Ports 80/443 usable without root", "ok"),
          Check("sysservices", "Bundled system services are disabled", "ok"),
          Check("ports", "Ports 80/443 are free or used by the PIPanel Nginx", "ok"),
          Check("units", "All PIPanel services are running", "ok"), Check("dns", "The .test domain resolves", "ok"),
          Check("ca", "Certificates are trusted", "ok")]

for MODE in ("light", "dark"):
    win.themer.apply(MODE)
    for i, p in enumerate(win.pages):
        win.show_page(i)
        settle()
        slug = p.title.lower()
        if slug == "logs":
            p.source.setCurrentText("blog (access)"); p.load(); settle()
        if slug == "services":
            shot("services")
            win.resize(1120, 900); p._doctor(DOCTOR); settle(); shot("services-doctor"); win.resize(1120, 720)
            continue
        if slug == "database":     # taller window so the Users and Root password cards are visible too
            win.resize(1120, 960); settle(10); shot(slug); win.resize(1120, 720)
            continue
        shot(slug)
        if slug == "php":
            p.ext_version.setCurrentIndex(p.ext_version.findData("8.4")); p.refresh_ext(); settle(10)
            p.ext.adjustSize(); p.ext.grab().save(str(OUT / f"{MODE}-php-extensions.png"))
    # the activity panel: one task running, one waiting for it (apt is serialized), one finished
    win.show_page(0); settle()
    gate = threading.Event()
    win.tasks.run(lambda: None, label="Installing Node 22…", lock="node")
    win.tasks.run(lambda: gate.wait(20), label="Installing MariaDB…", lock="system")
    win.tasks.run(lambda: None, label="Installing PHP 8.5…", lock="system")
    settle(10); shot("activity")
    gate.set(); settle(15)
    for jid in list(win.tasks.infos):
        win.tasks.dismiss(jid)
print(sorted(x.name for x in OUT.iterdir()))
