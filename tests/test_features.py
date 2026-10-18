"""Proxies, PHP on demand, Mailpit, Redis, Adminer, Xdebug, site processes, .env sync, backups, Cloudflare."""
import gzip
import io
import json
import shutil
import subprocess
import tarfile

import pytest

from pipanel.core import logs, processes
from pipanel.core.adminer import Adminer, AdminerError
from pipanel.core.cloudflare import Cloudflared, CloudflareError, parse_quicktunnel
from pipanel.core.config import SiteOverride
from pipanel.core.controller import Controller, ControllerError
from pipanel.core.mailpit import Mailpit
from pipanel.core.mysql import MySQL, MySQLError
from pipanel.core.php import PHP
from pipanel.core.privileged import HELPER_VERSION, Privileged
from pipanel.core.projects import dotenv_get
from pipanel.core.runner import Result
from pipanel.core.services import service_title, site_process_service, split_site_process, unit_name
from pipanel.core.sites import Site, normalize_target
from pipanel.core.templates import render_proxy, render_site, render_unit


# ---------- proxies ----------
@pytest.mark.parametrize("given,want", [
    ("3000", "http://127.0.0.1:3000"),
    ("localhost:5173", "http://localhost:5173"),
    ("http://127.0.0.1:8000/", "http://127.0.0.1:8000"),
    ("https://api.local:8443/v1", "https://api.local:8443/v1"),
    ("http://[::1]:3000", "http://[::1]:3000"),
])
def test_normalize_target(given, want):
    assert normalize_target(given) == want


@pytest.mark.parametrize("bad", ["", "70000", "ftp://x", "http://x:1;evil", "http://a b", "http://x/$host",
                                 "http://x:0", "http://x'y", "http://x/{y}"])
def test_normalize_target_rejects(bad):
    assert normalize_target(bad) is None


def test_render_proxy(paths):
    text = render_proxy(paths, name="app", target="http://127.0.0.1:3000", https=True, nginx_version="1.24.0",
                        local_only=True)
    assert "server_name app.test;" in text and "proxy_pass http://127.0.0.1:3000;" in text
    assert "listen 443 ssl http2;" in text and "Upgrade $http_upgrade" in text
    assert "allow 127.0.0.1;" in text and "deny all;" in text
    plain = render_proxy(paths, name="app", target="http://127.0.0.1:3000")
    assert "deny all" not in plain and "ssl" not in plain
    with pytest.raises(ValueError):
        render_proxy(paths, name="app", target="http://x;evil")


def test_render_site_local_only_and_unit_workdir(paths, tmp_path):
    text = render_site(paths, name="adminer", docroot=tmp_path, php_version="8.3", local_only=True)
    assert "allow 127.0.0.1;" in text
    assert "allow 127.0.0.1" not in render_site(paths, name="blog", docroot=tmp_path, php_version="8.3")
    unit = render_unit(description="x", exec_start="/bin/true", workdir=tmp_path / "my project")
    assert f"WorkingDirectory={tmp_path / 'my project'}" in unit
    assert "WorkingDirectory" not in render_unit(description="x", exec_start="/bin/true")


def test_service_names():
    assert site_process_service("my-blog", "vite") == "site-my-blog-vite"
    assert split_site_process("site-my-blog-vite") == ("my-blog", "vite")
    assert split_site_process("nginx") is None and split_site_process("site-") is None
    assert service_title("site-my-blog-queue") == "my-blog: Queue worker"
    assert service_title("mail") == "Mailpit" and service_title("cloudflared") == "Cloudflare Tunnel"


# ---------- controller fixture ----------
@pytest.fixture
def ctl(paths, runner, tmp_path, no_system_db):
    sbin = tmp_path / "sbin"; sbin.mkdir()
    for v in ("8.2", "8.3", "8.4"):
        (sbin / f"php-fpm{v}").write_text("")
    php = PHP(paths, etc_php=tmp_path / "etc", sbin=sbin, usr_bin=tmp_path)
    helper = tmp_path / "helper"; helper.write_text(f"HELPER_VERSION = {HELPER_VERSION}\n")
    c = Controller(paths, runner, home=tmp_path / "home", php=php, privileged=Privileged(runner, helper),
                   fetch=lambda u: b"")
    (tmp_path / "home").mkdir()
    c.services._nginx_version = "1.24.0"
    c.services.nginx_bin = str(tmp_path / "no-nginx")
    return c


def mkproj(root, name, laravel=False, env=None):
    d = root / name
    d.mkdir(parents=True)
    if laravel:
        (d / "artisan").write_text(""); (d / "public").mkdir(); (d / "public" / "index.php").write_text("")
        if env is not None:
            (d / ".env").write_text(env)
    else:
        (d / "index.php").write_text("")
    return d


def states_runner(runner, states: dict):
    """systemctl --user is-active answers from `states` (unit name -> state)."""
    def run(argv, **kw):
        runner.calls.append([str(a) for a in argv])
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return Result(0, "\n".join(states.get(u, "inactive") for u in argv[3:]) + "\n")
        return Result(0)
    runner.run = run


def test_add_and_remove_proxy(ctl, tmp_path, paths):
    mkproj(tmp_path / "proj", "blog")
    ctl.park(tmp_path / "proj")
    ctl.add_proxy("App", "3000")
    assert ctl.cfg.proxies == {"app": "http://127.0.0.1:3000"}
    assert "proxy_pass http://127.0.0.1:3000;" in (paths.nginx_sites / "app.conf").read_text()
    assert [x.name for x in ctl.proxies()] == ["app"]
    with pytest.raises(ControllerError):
        ctl.add_proxy("blog", "3000")        # taken by a site
    with pytest.raises(ControllerError):
        ctl.add_proxy("api", "http://x;y")
    with pytest.raises(ControllerError):
        ctl.add_proxy("mailpit", "3000")     # reserved for the tool
    again = Controller(paths, ctl.runner, home=ctl.home, php=ctl.php)
    assert again.cfg.proxies == {"app": "http://127.0.0.1:3000"}
    ctl.remove_proxy("app")
    assert not (paths.nginx_sites / "app.conf").exists()


def test_proxy_https_uses_its_certificate(ctl, paths, runner):
    ctl.add_proxy("app", "5173")

    def fake(argv, **kw):
        if argv[0] == "mkcert":
            c, k = ctl.certs.cert_paths("app", "test"); c.write_text("c"); k.write_text("k")
        return Result(0)
    runner.run = fake
    ctl.set_https("app", True)
    assert "listen 443 ssl" in (paths.nginx_sites / "app.conf").read_text()
    assert ctl.site_url(ctl.proxies()[0]) == "https://app.test"


# ---------- PHP on demand ----------
def test_php_in_use_and_reconcile(ctl, tmp_path, runner):
    mkproj(tmp_path / "proj", "blog")
    ctl.park(tmp_path / "proj")
    ctl.cfg.sites["blog"] = SiteOverride(php="8.2")
    ctl.cfg.default_php = "8.3"
    assert ctl.php_in_use() == ["8.2", "8.3"]
    states_runner(runner, {unit_name("nginx"): "active", unit_name("php8.3"): "active",
                           unit_name("php8.4"): "active"})
    ctl.apply()
    started = [c for c in runner.calls if c[2:3] == ["start"]]
    stopped = [c for c in runner.calls if c[2:3] == ["stop"]]
    assert started == [["systemctl", "--user", "start", unit_name("php8.2")]]
    assert stopped == [["systemctl", "--user", "stop", unit_name("php8.4")]]


def test_reconcile_starts_nothing_while_the_web_server_is_down(ctl, runner):
    states_runner(runner, {})
    ctl.apply()
    assert not [c for c in runner.calls if c[2:3] in (["start"], ["stop"])]


def test_start_all_starts_only_versions_in_use_and_addons(ctl, runner, paths, monkeypatch):
    (paths.systemd_user / unit_name("mail")).write_text("")
    monkeypatch.setattr(ctl.services, "failures", lambda names, **kw: {})
    ctl.cfg.autostart_services = False
    assert ctl.start_all().ok
    start = next(c for c in runner.calls if c[2:3] == ["start"] and unit_name("nginx") in c)
    assert unit_name("php8.3") in start and unit_name("php8.4") not in start and unit_name("mail") in start


def test_stop_all_stops_every_unit(ctl, runner, paths):
    for s in ("mail", "site-blog-vite", "cloudflared", "e2e-nginx"):
        (paths.systemd_user / unit_name(s)).write_text("")
    ctl.stop_all()
    stop = next(c for c in runner.calls if c[2:3] == ["stop"])
    for s in ("nginx", "php8.4", "mail", "site-blog-vite", "cloudflared"):
        assert unit_name(s) in stop
    assert unit_name("e2e-nginx") not in stop


def test_service_states_lists_running_extras_only(ctl, runner, paths):
    for s in ("site-blog-vite", "site-blog-queue", "ngrok", "redis"):
        (paths.systemd_user / unit_name(s)).write_text("")
    states_runner(runner, {unit_name("site-blog-vite"): "active", unit_name("redis"): "inactive"})
    st = ctl.service_states()
    assert "site-blog-vite" in st and "site-blog-queue" not in st and "ngrok" not in st
    assert st["redis"] == "inactive" and {"nginx", "dns", "php8.2", "php8.3", "php8.4"} <= set(st)


# ---------- .env ----------
def test_https_toggle_updates_app_url(ctl, tmp_path, runner):
    mkproj(tmp_path / "proj", "shop", laravel=True, env="APP_NAME=Shop\nAPP_URL=http://shop.test\n")
    mkproj(tmp_path / "proj", "mine", laravel=True, env="APP_URL=https://staging.example.com\n")
    ctl.park(tmp_path / "proj")

    def fake(argv, **kw):
        if argv[0] == "mkcert":
            name = argv[-2].split(".")[0]
            c, k = ctl.certs.cert_paths(name, "test"); c.write_text("c"); k.write_text("k")
        return Result(0)
    runner.run = fake
    rep = ctl.set_https("shop", True)
    assert dotenv_get(tmp_path / "proj" / "shop", "APP_URL") == "https://shop.test"
    assert any("APP_URL" in w for w in rep.warnings)
    ctl.set_https("mine", True)
    assert dotenv_get(tmp_path / "proj" / "mine", "APP_URL") == "https://staging.example.com"   # chosen on purpose
    ctl.set_ports(8080, 8443)
    assert dotenv_get(tmp_path / "proj" / "shop", "APP_URL") == "https://shop.test:8443"


def test_write_env_presets(ctl, tmp_path):
    proj = mkproj(tmp_path / "proj", "blog", laravel=True, env="DB_CONNECTION=sqlite\n# DB_HOST=127.0.0.1\n")
    ctl.park(tmp_path / "proj")
    ctl.write_env("blog", "database", "blog_db")
    assert dotenv_get(proj, "DB_CONNECTION") == "mysql" and dotenv_get(proj, "DB_DATABASE") == "blog_db"
    ctl.write_env("blog", "mail")
    assert dotenv_get(proj, "MAIL_PORT") == "1025" and dotenv_get(proj, "MAIL_MAILER") == "smtp"
    ctl.write_env("blog", "redis")
    assert dotenv_get(proj, "REDIS_HOST") == "127.0.0.1"
    with pytest.raises(ControllerError):
        ctl.write_env("blog", "nope")


# ---------- Xdebug and VS Code ----------
def test_vscode_launch_creates_and_merges(ctl, tmp_path):
    mkproj(tmp_path / "proj", "blog", laravel=True)
    ctl.park(tmp_path / "proj")
    f = ctl.vscode_launch("blog")
    data = json.loads(f.read_text())
    assert data["configurations"][0]["port"] == 9003
    ctl.vscode_launch("blog")
    assert len(json.loads(f.read_text())["configurations"]) == 1   # not added twice
    f.write_text(json.dumps({"version": "0.2.0", "configurations": [{"name": "Node", "type": "node"}]}))
    ctl.vscode_launch("blog")
    assert [c["type"] for c in json.loads(f.read_text())["configurations"]] == ["node", "php"]
    f.write_text('{\n  // a comment\n  "configurations": []\n}')
    with pytest.raises(ControllerError, match="9003"):
        ctl.vscode_launch("blog")
    assert "// a comment" in f.read_text()   # left alone


def test_set_xdebug_restarts_running_fpm(ctl, tmp_path, runner):
    mods = tmp_path / "etc" / "8.3" / "mods-available"; mods.mkdir(parents=True)
    (mods / "xdebug.ini").write_text("zend_extension=xdebug.so\n")
    ctl.php.ensure_conf_d("8.3")
    states_runner(runner, {unit_name("php8.3"): "active"})
    ctl.set_xdebug("8.3", True)
    assert ctl.php.xdebug_state("8.3") == "on"
    assert ["systemctl", "--user", "restart", unit_name("php8.3")] in runner.calls
    with pytest.raises(ControllerError):
        ctl.set_xdebug("8.4", True)


# ---------- site processes ----------
def test_available_processes(tmp_path):
    proj = mkproj(tmp_path, "blog", laravel=True)
    s = Site("blog", proj, proj / "public", "laravel", "8.3", False)
    assert [p.key for p in processes.available(s)] == ["queue", "schedule"]
    (proj / "package.json").write_text(json.dumps({"scripts": {"dev": "vite", "build": "vite build"}}))
    (proj / "composer.json").write_text(json.dumps({"require": {"laravel/reverb": "^1.0"}}))
    assert [p.key for p in processes.available(s)] == ["vite", "queue", "schedule", "reverb"]
    plain = mkproj(tmp_path, "spa")
    (plain / "package.json").write_text("{not json")
    assert processes.available(Site("spa", plain, plain, "php", "8.3", False)) == []


def test_start_process_writes_unit_with_site_versions(ctl, tmp_path, runner, paths, monkeypatch):
    proj = mkproj(tmp_path / "proj", "blog", laravel=True)
    (proj / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}}))
    ctl.park(tmp_path / "proj")
    ctl.cfg.sites["blog"] = SiteOverride(php="8.2")
    monkeypatch.setattr(ctl.services, "failures", lambda names, **kw: {})
    ctl.start_process("blog", "queue")
    unit = (paths.systemd_user / unit_name("site-blog-queue")).read_text()
    assert f"WorkingDirectory={proj}" in unit and "php8.2 artisan queue:listen --tries=1" in unit
    assert f"PHP_INI_SCAN_DIR={ctl.php.conf_d('8.2')}" in unit
    assert ["systemctl", "--user", "restart", unit_name("site-blog-queue")] in runner.calls
    with pytest.raises(ControllerError, match="fnm"):
        ctl.start_process("blog", "vite")
    ctl.node.fnm_bin.write_text("")
    ctl.start_process("blog", "vite")
    unit = (paths.systemd_user / unit_name("site-blog-vite")).read_text()
    assert "exec --using default npm run dev" in unit and "FNM_DIR=" in unit
    (proj / ".nvmrc").write_text("20\n")
    ctl.start_process("blog", "vite")
    assert "exec npm run dev" in (paths.systemd_user / unit_name("site-blog-vite")).read_text()
    with pytest.raises(ControllerError):
        ctl.start_process("blog", "horizon")   # not installed in this project


def test_start_process_that_dies_is_stopped_and_reported(ctl, tmp_path, runner, monkeypatch):
    mkproj(tmp_path / "proj", "blog", laravel=True)
    ctl.park(tmp_path / "proj")
    monkeypatch.setattr(ctl.services, "failures", lambda names, **kw: {names[0]: "Could not open input file"})
    with pytest.raises(ControllerError, match="Could not open input file"):
        ctl.start_process("blog", "schedule")
    assert ["systemctl", "--user", "stop", unit_name("site-blog-schedule")] in runner.calls


# ---------- Mailpit, Adminer, Redis ----------
def _tgz(files: dict):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as t:
        for n, data in files.items():
            info = tarfile.TarInfo(n); info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return b.getvalue()


def test_install_mailpit_serves_inbox_locally(ctl, paths, runner):
    ctl.fetch = lambda u: _tgz({"LICENSE": b"x", "mailpit": b"BIN"})
    assert ctl.install_mailpit().ok
    assert ctl.mailpit.bin.read_bytes() == b"BIN" and ctl.mailpit.bin.stat().st_mode & 0o100
    unit = (paths.systemd_user / unit_name("mail")).read_text()
    assert "--smtp 127.0.0.1:1025 --listen 127.0.0.1:8025" in unit
    site = (paths.nginx_sites / "mailpit.conf").read_text()
    assert "proxy_pass http://127.0.0.1:8025;" in site and "deny all;" in site
    assert ["systemctl", "--user", "start", unit_name("mail")] in runner.calls


def test_mailpit_archive_without_binary(paths):
    with pytest.raises(Exception):
        Mailpit(paths).install(lambda u: _tgz({"README.md": b""}), "x86_64")


def test_adminer_install_and_signin_wrapper(ctl, paths, tmp_path):
    with pytest.raises(AdminerError):
        Adminer(paths).install(lambda u: b"<html>")
    ctl.fetch = lambda u: b"<?php\n/** Adminer - Compact database management */"
    ctl.install_adminer()
    index = (ctl.adminer.root / "index.php").read_text()
    assert str(ctl.mysql.client_cnf) in index and "127.0.0.1:3306" in index
    conf = (paths.nginx_sites / "adminer.conf").read_text()
    assert f'root "{ctl.adminer.root}";' in conf and "deny all;" in conf
    ctl.set_db_engine("mariadb")
    assert "mariadb/client.cnf" in (ctl.adminer.root / "index.php").read_text()


@pytest.mark.skipif(not shutil.which("php"), reason="needs a PHP CLI to lint the wrapper")
def test_adminer_wrapper_is_valid_php(paths, tmp_path):
    a = Adminer(paths)
    a.write_index(tmp_path / "it's" / "client.cnf", 3306)
    r = subprocess.run(["php", "-l", str(a.root / "index.php")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(not shutil.which("php"), reason="needs a PHP CLI")
def test_adminer_wrapper_reads_the_password(paths, tmp_path):
    m = MySQL(paths, None)
    m._write_client('pa"ss\\word')
    a = Adminer(paths)
    a.write_index(m.client_cnf, 3306)
    from pipanel.core.adminer import PASSWORD_PHP, php_str
    php = PASSWORD_PHP + f"echo pipanel_password({php_str(str(m.client_cnf))});"
    r = subprocess.run(["php", "-r", php], capture_output=True, text=True)
    assert r.stdout == 'pa"ss\\word', r.stderr


def test_install_redis(ctl, paths, runner):
    steps = ctl.install_redis()
    assert all(r.ok for _, r in steps)
    assert runner.ran("pkexec", str(ctl.priv.helper), "install-redis")
    conf = ctl.redis.conf.read_text()
    assert "port 6379" in conf and "bind 127.0.0.1" in conf and f"dir {paths.data / 'redis'}" in conf
    assert "redis-server" in (paths.systemd_user / unit_name("redis")).read_text()


def test_redis_needs_a_current_helper(ctl, runner):
    ctl.priv.helper.write_text("# helper from before versioning\n")
    assert ctl.priv.outdated()
    steps = ctl.install_redis()
    assert not steps[0][1].ok and "install.sh" in steps[0][1].text
    assert not runner.ran("pkexec")


# ---------- backups ----------
@pytest.fixture
def db(paths, runner, tmp_path):
    dump = tmp_path / "mysqldump"; dump.write_text("")
    import dataclasses
    m = MySQL(paths, runner)
    m.engine = dataclasses.replace(m.engine, dump=str(dump))
    runner.on(["mysql", f"--defaults-extra-file={m.client_cnf}", "-N", "-B"], Result(0, "blog\n"))
    return m


def test_dump_gzip(db, runner, tmp_path):
    dest = tmp_path / "out" / "blog.sql.gz"

    def fake(argv, **kw):
        runner.calls.append(argv)
        if argv[0] == db.engine.dump:
            out = next(a for a in argv if a.startswith("--result-file="))[len("--result-file="):]
            open(out, "w").write("CREATE TABLE t (id int);\n")
        return Result(0)
    runner.run = fake
    assert db.dump("blog", dest) == dest
    assert gzip.open(dest).read() == b"CREATE TABLE t (id int);\n"
    assert not (tmp_path / "out" / "blog.sql").exists()
    cmd = runner.calls[-1]
    assert cmd[1] == f"--defaults-extra-file={db.client_cnf}" and "--single-transaction" in cmd and cmd[-1] == "blog"
    with pytest.raises(MySQLError):
        db.dump("bad name", dest)


def test_dump_reports_missing_tool(paths, runner, tmp_path):
    m = MySQL(paths, runner, engine="mariadb")
    import dataclasses
    m.engine = dataclasses.replace(m.engine, dump=str(tmp_path / "missing"))
    with pytest.raises(MySQLError, match="not installed"):
        m.dump("blog", tmp_path / "x.sql")


def test_import_gz_streams_a_plain_file(db, runner, tmp_path):
    src = tmp_path / "in.sql.gz"
    with gzip.open(src, "wb") as f:
        f.write(b"INSERT INTO t VALUES (1);\n")
    seen = {}

    queries = []

    def fake(argv, **kw):
        runner.calls.append(argv)
        if kw.get("stdin_file"):
            seen["sql"] = open(kw["stdin_file"]).read()
            seen["argv"] = argv
        if argv[-2:] == ["-N", "-B"]:
            queries.append(kw.get("input"))
            return Result(0, "blog\n")
        return Result(0)
    runner.run = fake
    db.import_file("blog", src)
    assert seen["sql"] == "INSERT INTO t VALUES (1);\n" and seen["argv"][-1] == "blog"
    assert not list(db.backups.glob(".import-*"))   # the unpacked copy is removed
    assert not any("CREATE DATABASE" in q for q in queries)
    db.import_file("newdb", src)
    assert any("CREATE DATABASE `newdb`" in q for q in queries)


def test_clone_refuses_existing_target(db):
    with pytest.raises(MySQLError, match="exists"):
        db.clone("blog", "blog")


def test_runner_streams_stdin_file(tmp_path):
    from pipanel.core.runner import Runner
    f = tmp_path / "in.txt"; f.write_text("hello\n")
    assert Runner().run(["cat"], stdin_file=str(f)).out == "hello\n"


# ---------- logs ----------
LOG = """[2026-10-18 09:00:00] local.DEBUG: cache miss
[2026-10-18 09:00:01] local.ERROR: Undefined variable $x {"exception":"[object] (ErrorException(code: 0)
#0 /app/routes/web.php(12): handle()
#1 {main}
"}
[2026-10-18 09:00:02] local.WARNING: slow query
[2026-10-18 09:00:03] production.CRITICAL: down
"""


def test_filter_level_keeps_stack_traces():
    out = logs.filter_level(LOG, "error")
    assert "Undefined variable" in out and "#1 {main}" in out and "CRITICAL" in out
    assert "cache miss" not in out and "slow query" not in out
    assert "slow query" in logs.filter_level(LOG, "warning")
    assert logs.filter_level(LOG, "debug") == LOG


def test_laravel_log_sources_and_daily_files(paths, tmp_path):
    proj = tmp_path / "blog"
    (proj / "storage" / "logs").mkdir(parents=True)
    assert logs.laravel_log(proj) == proj / "storage/logs/laravel.log"
    daily = proj / "storage/logs/laravel-2026-10-18.log"; daily.write_text("x")
    assert logs.laravel_log(proj) == daily
    srcs = logs.sources(paths, ["blog", "plain"], ["8.3"], laravel={"blog": proj}, units=["mail", "site-blog-vite"])
    labels = [s.label for s in srcs]
    assert "blog (laravel.log)" in labels and "plain (laravel.log)" not in labels
    assert "Mailpit" in labels and "blog: Vite" in labels
    assert next(s for s in srcs if s.label == "blog (laravel.log)").laravel
    logs.clear(daily)
    assert daily.read_text() == ""


# ---------- Cloudflare ----------
def test_cloudflare_quicktunnel_parsing_and_unit(paths):
    assert parse_quicktunnel('{"hostname":"a-b-c.trycloudflare.com"}') == "https://a-b-c.trycloudflare.com"
    assert parse_quicktunnel('{"hostname":""}') is None and parse_quicktunnel("nope") is None
    c = Cloudflared(paths)
    with pytest.raises(CloudflareError):
        c.install(lambda u: b"<html>", "x86_64")
    c.install(lambda u: b"\x7fELF...", "x86_64")
    assert c.installed()
    spec = c.unit_spec("blog", port=8080)
    assert "--url http://127.0.0.1:8080 --http-host-header blog.test" in " ".join(spec["argv"])
    with pytest.raises(CloudflareError):
        c.unit_spec("bad name")


def test_share_with_cloudflare_needs_no_account(ctl, tmp_path, runner, paths, monkeypatch):
    mkproj(tmp_path / "proj", "blog")
    ctl.park(tmp_path / "proj")
    ctl.fetch = lambda u: b"\x7fELF"
    ctl.set_share_provider("cloudflare")
    monkeypatch.setattr(ctl.services, "failures", lambda names, **kw: {})
    ctl.share("blog")
    assert (paths.systemd_user / unit_name("cloudflared")).exists()
    assert ["systemctl", "--user", "stop", unit_name("ngrok")] in runner.calls
    assert ["systemctl", "--user", "restart", unit_name("cloudflared")] in runner.calls
    with pytest.raises(ControllerError):
        ctl.set_share_provider("bogus")
    ctl.stop_share()
    assert ["systemctl", "--user", "stop", unit_name("ngrok"), unit_name("cloudflared")] in runner.calls


# ---------- helper ----------
def test_helper_version_matches(tmp_path):
    from pathlib import Path
    helper = Path(__file__).resolve().parent.parent / "packaging" / "pipanel-helper"
    assert Privileged(None, helper).version() == HELPER_VERSION
    old = tmp_path / "old"; old.write_text("#!/usr/bin/python3\n")
    assert Privileged(None, old).version() == 1 and Privileged(None, old).outdated()
    assert not Privileged(None, tmp_path / "missing").outdated()


# ---------- WordPress ----------
def test_wordpress_install_verifies_checksum(paths, runner):
    import hashlib
    from pipanel.core.wordpress import PHAR_URL, WordPress, WordPressError
    wp = WordPress(paths, runner, PHP(paths))
    files = {PHAR_URL: b"PHAR", PHAR_URL + ".sha512": hashlib.sha512(b"PHAR").hexdigest().encode() + b"\n"}
    w = wp.install(files.get)
    assert wp.installed() and "wp-cli.phar" in w.read_text()
    files[PHAR_URL + ".sha512"] = b"0" * 128
    with pytest.raises(WordPressError):
        wp.install(files.get)


def test_new_wordpress_keeps_passwords_off_the_command_line(ctl, tmp_path, runner):
    (tmp_path / "proj").mkdir()
    ctl.park(tmp_path / "proj")
    ctl.wordpress.phar.write_text("")
    (ctl.mysql.datadir / "mysql").mkdir(parents=True)
    ctl.mysql.set_root_password = None
    ctl.mysql._write_client("s3cret")
    seen = []

    def fake(argv, **kw):
        argv = [str(a) for a in argv]
        seen.append((argv, kw.get("input")))
        if argv[-2:] == ["-N", "-B"]:
            return Result(0, "1\n" if kw.get("input") == "SELECT 1" else "")
        return Result(0)
    runner.run = fake
    site = ctl.new_wordpress("Press")
    assert site.url == "http://press.test" and site.database == "press" and len(site.admin_password) >= 12
    wp_calls = [(a, i) for a, i in seen if str(ctl.wordpress.phar) in a]
    assert [a[a.index(str(ctl.wordpress.phar)) + 1] for a, _ in wp_calls] == ["core", "config", "core"]
    config, install = wp_calls[1], wp_calls[2]
    assert "--dbname=press" in config[0] and "--prompt=dbpass" in config[0] and config[1] == "s3cret\n"
    assert install[1] == site.admin_password + "\n" and "--url=http://press.test" in install[0]
    assert not any("s3cret" in " ".join(a) or site.admin_password in " ".join(a) for a, _ in seen)
    assert any("CREATE DATABASE `press`" in (i or "") for _, i in seen)
    assert ctl.site("press").path == tmp_path / "proj" / "Press"


def test_new_wordpress_needs_a_running_database(ctl, tmp_path):
    (tmp_path / "proj").mkdir()
    ctl.park(tmp_path / "proj")
    with pytest.raises(ControllerError, match="needs the database"):
        ctl.new_wordpress("press")


def test_doctor_offers_the_backup_tool(ctl, runner):
    import dataclasses
    (ctl.mysql.datadir / "mysql").mkdir(parents=True)
    ctl.mysql.engine = dataclasses.replace(ctl.mysql.engine, dump="/nonexistent/mariadb-dump")
    checks = {c.id: c for c in ctl.doctor()}
    assert checks["dbdump"].fix == "install_db_tools"
    ctl.fix("install_db_tools")
    assert runner.ran("pkexec", str(ctl.priv.helper), "install-database", "mysql")


# ---------- tools remember being turned off ----------
def test_stopped_tool_stays_off_until_started_again(ctl, runner, paths, monkeypatch):
    for s in ("mail", "redis"):
        (paths.systemd_user / unit_name(s)).write_text("")
    monkeypatch.setattr(ctl.services, "failures", lambda names, **kw: {})
    ctl.stop_service("mail")
    assert ctl.cfg.stopped_tools == ["mail"]
    assert ["systemctl", "--user", "disable", unit_name("mail")] in runner.calls   # not at the next login
    assert Controller(paths, runner, home=ctl.home, php=ctl.php).cfg.stopped_tools == ["mail"]   # saved

    runner.calls.clear()
    ctl.start_all()
    start = next(c for c in runner.calls if c[2:3] == ["start"])
    enable = next(c for c in runner.calls if c[2:3] == ["enable"])
    assert unit_name("redis") in start and unit_name("mail") not in start and unit_name("mail") not in enable

    ctl.stop_all()   # "Stop all" stops it for now, but does not forget that Redis was on
    assert ctl.cfg.stopped_tools == ["mail"]

    runner.calls.clear()
    ctl.start_service("mail")
    assert ctl.cfg.stopped_tools == []
    assert ["systemctl", "--user", "enable", unit_name("mail")] in runner.calls


def test_core_services_are_not_remembered(ctl, runner):
    ctl.stop_service("nginx")
    assert ctl.cfg.stopped_tools == []
    assert not any(c[2:3] == ["disable"] for c in runner.calls)


def test_starting_a_tool_without_autostart_does_not_enable_it(ctl, runner):
    ctl.cfg.autostart_services = False
    ctl.cfg.stopped_tools = ["redis"]
    ctl.restart_service("redis")
    assert ctl.cfg.stopped_tools == [] and not any(c[2:3] == ["enable"] for c in runner.calls)


def test_autostart_toggle_skips_stopped_tools(ctl, runner, paths):
    for s in ("mail", "redis"):
        (paths.systemd_user / unit_name(s)).write_text("")
    ctl.cfg.stopped_tools = ["redis"]
    ctl.set_autostart_services(True)
    enable = next(c for c in runner.calls if c[2:3] == ["enable"])
    assert unit_name("mail") in enable and unit_name("redis") not in enable
    ctl.set_autostart_services(False)
    disable = next(c for c in runner.calls if c[2:3] == ["disable"])
    assert unit_name("mail") in disable and unit_name("redis") in disable


def test_stopped_tools_are_validated(paths):
    from pipanel.core import config as cfgmod
    paths.config_file.write_text(json.dumps({"stopped_tools": ["mail", "nginx", 3]}))
    assert cfgmod.load(paths.config_file).stopped_tools == ["mail"]


# ---------- new folders become sites on their own ----------
def test_watcher_units_follow_the_projects_folders(ctl, tmp_path, paths, runner):
    import sys
    a, b = tmp_path / "projA", tmp_path / "my 100% projects"
    a.mkdir(), b.mkdir()
    ctl.park(a)
    ctl.apply()
    path_unit = paths.systemd_user / "pipanel-watch.path"
    assert f"PathChanged={a}" in path_unit.read_text()
    svc = (paths.systemd_user / unit_name("watch")).read_text()
    assert f"ExecStart={sys.executable} -m pipanel sync --settle" in svc
    assert f"XDG_CONFIG_HOME={paths.config.parent}" in svc
    assert ["systemctl", "--user", "restart", "pipanel-watch.path"] in runner.calls
    runner.calls.clear()
    ctl.apply()
    assert ["systemctl", "--user", "restart", "pipanel-watch.path"] not in runner.calls   # unchanged
    ctl.park(b)
    ctl.apply()
    assert "PathChanged=" + str(b).replace("%", "%%") in path_unit.read_text()
    ctl.unpark(str(a)); ctl.unpark(str(b))
    ctl.apply()
    assert not path_unit.exists() and not (paths.systemd_user / unit_name("watch")).exists()


def test_watcher_follows_the_autostart_setting(ctl, tmp_path, runner):
    (tmp_path / "p").mkdir()
    ctl.park(tmp_path / "p")
    ctl.cfg.autostart_services = False
    ctl.apply()
    assert ["systemctl", "--user", "disable", "pipanel-watch.path"] in runner.calls
    ctl.set_autostart_services(True)
    assert ["systemctl", "--user", "enable", "pipanel-watch.path"] in runner.calls


def test_settle_follows_a_folder_that_is_still_filling(tmp_path):
    from pipanel.core import watch
    from pipanel.core.sites import detect
    root = tmp_path / "proj"
    (root / "shop").mkdir(parents=True)
    import time
    t = {"now": 0.0}
    base = time.time()
    applied = []

    def sites():
        out = []
        for d in sorted(root.iterdir()):
            kind, docroot = detect(d)
            out.append(Site(d.name, d, docroot, kind, "8.3", False))
        return out

    def sleep(s):
        t["now"] += s
        if t["now"] == 9:     # `laravel new` has unpacked artisan and public/ by now
            (root / "shop" / "artisan").write_text("")
            (root / "shop" / "public").mkdir()
        if t["now"] == 30:    # another folder appears while the service is still running
            (root / "blog").mkdir()

    n = watch.settle(sites, lambda: applied.append(sorted(watch.signature(sites()).items())),
                     clock=lambda: t["now"], wall=lambda: base + t["now"], sleep=sleep,
                     quiet=20, step=3)
    assert n == 3
    shop = dict(applied[1])["shop"]   # (path, kind, docroot)
    assert shop[1] == "laravel" and shop[2].endswith("public")
    assert [name for name, _ in applied[2]] == ["blog", "shop"]
    # the empty "blog" folder could still be filling: watched for YOUNG seconds after it appeared, then it stops
    assert watch.YOUNG <= t["now"] < watch.LIMIT


def test_settle_stops_quickly_when_nothing_changes(tmp_path):
    from pipanel.core import watch
    t = {"now": 0.0}
    n = watch.settle(lambda: [], lambda: None, clock=lambda: t["now"], sleep=lambda s: t.update(now=t["now"] + s))
    assert n == 1 and t["now"] <= watch.QUIET + watch.STEP


def test_path_unit_escapes_percent():
    from pipanel.core.watch import render_path_unit
    text = render_path_unit(["/home/u/100% done", "/home/u/projects"])
    assert "PathChanged=/home/u/100%% done\n" in text and "WantedBy=default.target" in text


# ---------- a Laravel project fresh from Git ----------
def cloned(root, name="ft", example="APP_KEY=\nDB_CONNECTION=mysql\nDB_HOST=127.0.0.1\nDB_DATABASE=beranda-laravel\n"
                                     "DB_USERNAME=root\nDB_PASSWORD=\nAPP_URL=http://localhost\n"):
    d = mkproj(root, name, laravel=True)
    (d / "composer.json").write_text("{}")
    (d / ".env.example").write_text(example)
    (d / "storage" / "app" / "public").mkdir(parents=True)
    (d / "database" / "dumps").mkdir(parents=True)
    return d


def test_setup_needs_and_dumps(tmp_path):
    from pipanel.core import projects
    d = cloned(tmp_path)
    assert projects.setup_needs(d) == ["vendor", "env"]
    (d / "vendor").mkdir(); (d / "vendor" / "autoload.php").write_text("")
    (d / ".env").write_text("APP_KEY=\n")
    assert projects.setup_needs(d) == ["key"]
    (d / ".env").write_text("APP_KEY=base64:abc\n")
    assert projects.setup_needs(d) == []
    (d / "database" / "dumps" / "prod.sql.gz").write_bytes(b"")
    (d / "vendor" / "x.sql").write_text("")          # inside vendor: not a dump of the project
    assert [f.name for f in projects.sql_dumps(d)] == ["prod.sql.gz"]


def test_setup_laravel_runs_every_missing_step(ctl, tmp_path, runner):
    d = cloned(tmp_path / "proj")
    dump = d / "database" / "dumps" / "local.sql"
    dump.write_text("CREATE TABLE t (id int);")
    ctl.park(tmp_path / "proj")
    (ctl.paths.bin / "composer.phar").write_text("")
    (ctl.mysql.datadir / "mysql").mkdir(parents=True)
    ctl.mysql._write_client("pw")
    queries, imported = [], []

    def fake(argv, **kw):
        argv = [str(a) for a in argv]
        runner.calls.append(argv)
        if argv[-2:] == ["-N", "-B"]:
            queries.append(kw.get("input"))
            return Result(0, "1\n" if kw.get("input") == "SELECT 1" else "")
        if kw.get("stdin_file"):
            imported.append(kw["stdin_file"])
        if "key:generate" in argv:
            projects_set = __import__("pipanel.core.projects", fromlist=["set_env"]).set_env
            projects_set(d, {"APP_KEY": "base64:generated"})
        return Result(0)
    runner.run = fake
    steps = ctl.setup_laravel("ft", database="import", dump=dump)
    names = [n for n, _ in steps]
    assert all(r.ok for _, r in steps), steps
    assert names[0] == "composer install" and "Create .env from .env.example" in names
    assert "php artisan key:generate" in names and "Create database beranda-laravel" in names
    assert "Import local.sql into beranda-laravel" in names and "php artisan storage:link" in names
    assert imported == [str(dump)]
    assert any("CREATE DATABASE `beranda-laravel`" in (q or "") for q in queries)
    assert dotenv_get(d, "DB_PASSWORD") == "pw" and dotenv_get(d, "APP_URL") == "http://ft.test"
    composer = next(c for c in runner.calls if "install" in c and any("composer.phar" in a for a in c))
    assert composer[0].endswith("php8.3")   # with the site's PHP version


def test_setup_laravel_explains_a_stopped_database(ctl, tmp_path, runner):
    d = cloned(tmp_path / "proj")
    (d / "vendor").mkdir(); (d / "vendor" / "autoload.php").write_text("")
    (d / ".env").write_text("APP_KEY=base64:x\nDB_CONNECTION=mysql\nDB_DATABASE=shop\n")
    ctl.park(tmp_path / "proj")
    runner.on(["mysql"], Result(1, "", "Can't connect"))
    steps = ctl.setup_laravel("ft")
    db = dict(steps)["Database"]
    assert not db.ok and "is not running" in db.text
    assert not any("migrate" in n for n, _ in steps)


def test_setup_laravel_with_sqlite(ctl, tmp_path, runner):
    d = cloned(tmp_path / "proj", example="APP_KEY=\nDB_CONNECTION=sqlite\n")
    (d / "vendor").mkdir(); (d / "vendor" / "autoload.php").write_text("")
    ctl.park(tmp_path / "proj")
    steps = ctl.setup_laravel("ft")
    assert (d / "database" / "database.sqlite").exists()
    assert [n for n, _ in steps][-2:] == ["php artisan migrate", "php artisan storage:link"]


def test_database_names_may_contain_a_dash():
    from pipanel.core.mysql import ident
    assert ident("beranda-laravel") == "`beranda-laravel`"
    with pytest.raises(MySQLError):
        ident("bad`name")


def test_template_mismatch_is_explained():
    from pipanel.core import templates
    with pytest.raises(templates.TemplateMismatch, match="open it again"):
        templates._render("site.conf.j2", name="x")


# ---------- phpMyAdmin and the database table ----------
def _pma_zip(files: dict) -> bytes:
    import zipfile as _zip
    b = io.BytesIO()
    with _zip.ZipFile(b, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    return b.getvalue()


def test_phpmyadmin_install_config_and_php_version(ctl, paths, tmp_path):
    import hashlib
    from pipanel.core.phpmyadmin import URL, PhpMyAdminError, pick_php
    data = _pma_zip({"phpMyAdmin-5.2.3-english/index.php": "<?php", "phpMyAdmin-5.2.3-english/js/a.js": "x"})
    files = {URL: data, URL + ".sha256": hashlib.sha256(data).hexdigest().encode() + b"  x.zip\n"}
    ctl.fetch = files.get
    ctl.install_phpmyadmin()
    root = ctl.phpmyadmin.root
    assert (root / "index.php").exists() and (root / "js" / "a.js").exists()
    cfg = (root / "config.inc.php").read_text()
    assert str(ctl.mysql.client_cnf) in cfg and "'auth_type'] = 'config'" in cfg and "AllowNoPassword" in cfg
    secret = (paths.data / "phpmyadmin-secret").read_text()
    assert len(secret) == 32 and secret in cfg
    conf = (paths.nginx_sites / "phpmyadmin.conf").read_text()
    assert "deny all;" in conf and "php8.3.sock" in conf          # 8.4 is too new for phpMyAdmin 5.2
    assert "8.3" in ctl.php_in_use()
    assert pick_php(["8.4", "8.5"], "8.4") == "8.4" and pick_php(["8.1", "8.2", "8.4"], "8.4") == "8.2"
    assert ctl.db_tools() == {"phpMyAdmin": "http://phpmyadmin.test"}
    assert ctl.db_tool_url("phpMyAdmin", "shop-db") == "http://phpmyadmin.test/index.php?route=/database/structure&db=shop-db"
    files[URL + ".sha256"] = b"0" * 64
    with pytest.raises(PhpMyAdminError):
        ctl.phpmyadmin.install(files.get)
    evil = _pma_zip({"pma/../../escape.php": "x"})
    with pytest.raises(PhpMyAdminError):
        ctl.phpmyadmin.install({URL: evil, URL + ".sha256": hashlib.sha256(evil).hexdigest().encode()}.get)
    assert not (paths.data.parent / "escape.php").exists()


@pytest.mark.skipif(not shutil.which("php"), reason="needs a PHP CLI")
def test_phpmyadmin_config_is_valid_php(paths, tmp_path):
    from pipanel.core.phpmyadmin import PhpMyAdmin
    pma = PhpMyAdmin(paths)
    pma.root.mkdir(parents=True)
    pma.write_config(tmp_path / "it's" / "client.cnf", 3306)
    r = subprocess.run(["php", "-l", str(pma.root / "config.inc.php")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not pma.write_config(tmp_path / "it's" / "client.cnf", 3306)   # same secret: unchanged


def test_database_stats(paths, runner):
    m = MySQL(paths, runner)
    runner.on(["mysql"], Result(0, "blog\t12\t49152\nmysql\t30\t1000\nempty\t0\t0\n"))
    assert m.database_stats() == [("blog", 12, 49152), ("empty", 0, 0)]
