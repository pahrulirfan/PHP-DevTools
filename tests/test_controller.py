import pytest

from pipanel.core.controller import Controller, ControllerError
from pipanel.core.mysql import MySQL
from pipanel.core.php import PHP
from pipanel.core.privileged import Privileged
from pipanel.core.runner import Result


@pytest.fixture
def ctl(paths, runner, tmp_path, no_system_db):
    sbin = tmp_path / "sbin"; sbin.mkdir()
    (sbin / "php-fpm8.3").write_text(""); (sbin / "php-fpm8.2").write_text("")
    php = PHP(paths, etc_php=tmp_path / "etc", sbin=sbin, usr_bin=tmp_path)
    helper = tmp_path / "helper"; helper.write_text("")
    c = Controller(paths, runner, home=tmp_path / "home", php=php, privileged=Privileged(runner, helper),
                   fetch=lambda u: b"")
    (tmp_path / "home").mkdir()
    c.services._nginx_version = "1.24.0"
    c.services.nginx_bin = str(tmp_path / "no-nginx")
    return c


def mkproj(root, name, laravel=False):
    d = root / name
    d.mkdir(parents=True)
    if laravel:
        (d / "artisan").write_text(""); (d / "public").mkdir(); (d / "public" / "index.php").write_text("")
    else:
        (d / "index.php").write_text("")
    return d


def test_park_apply_and_persist(ctl, tmp_path, paths):
    mkproj(tmp_path / "proj", "blog", laravel=True)
    ctl.park(tmp_path / "proj")
    assert [s.name for s in ctl.sites()] == ["blog"]
    ctl.apply()
    assert (paths.nginx_sites / "blog.conf").exists()
    again = Controller(paths, ctl.runner, home=ctl.home, php=ctl.php)
    assert again.cfg.parked == [str(tmp_path / "proj")]


def test_park_rejects_non_folder(ctl, tmp_path):
    with pytest.raises(ControllerError):
        ctl.park(tmp_path / "nope")


def test_link_validation(ctl, tmp_path):
    d = mkproj(tmp_path, "x")
    with pytest.raises(ControllerError):
        ctl.link("Bad Name", d)
    ctl.link("Mine", d)
    assert ctl.site("mine").linked
    ctl.unlink("mine")
    with pytest.raises(ControllerError):
        ctl.site("mine")


def test_set_php_and_https(ctl, tmp_path, paths, runner):
    mkproj(tmp_path / "proj", "blog")
    ctl.park(tmp_path / "proj")
    ctl.set_site_php("blog", "8.2")
    assert "php8.2.sock" in (paths.nginx_sites / "blog.conf").read_text()
    with pytest.raises(ControllerError):
        ctl.set_site_php("blog", "9.9")

    def fake_mkcert(argv, **kw):
        if argv[0] == "mkcert":
            c, k = ctl.certs.cert_paths("blog", "test"); c.write_text("c"); k.write_text("k")
        return Result(0)
    runner.run = fake_mkcert
    ctl.set_https("blog", True)
    assert ctl.site("blog").https and "listen 443 ssl" in (paths.nginx_sites / "blog.conf").read_text()
    assert ctl.site_url(ctl.site("blog")) == "https://blog.test"


def test_apply_reloads_only_active(ctl, tmp_path, runner):
    mkproj(tmp_path / "proj", "a")
    ctl.park(tmp_path / "proj")
    runner.on(["systemctl", "--user", "is-active"], Result(0, "active\ninactive\ninactive\n"))
    ctl.apply()
    reloads = [c for c in runner.calls if c[2:3] == ["reload"]]
    assert reloads == [["systemctl", "--user", "reload", "pipanel-nginx.service"]]


def test_remove_php_guards(ctl, tmp_path):
    mkproj(tmp_path / "proj", "a")
    ctl.park(tmp_path / "proj")
    with pytest.raises(ControllerError, match="default"):
        ctl.remove_php("8.3")
    ctl.set_site_php("a", "8.2")
    with pytest.raises(ControllerError, match="a"):
        ctl.remove_php("8.2")


def test_set_default_php_requires_installed(ctl):
    with pytest.raises(ControllerError):
        ctl.set_default_php("8.4")
    ctl.set_default_php("8.2")
    assert ctl.php.cli_version() == "8.2" and ctl.cfg.default_php == "8.2"


def test_php_settings_validated(ctl):
    with pytest.raises(ControllerError):
        ctl.set_php_settings({"memory_limit": "lots"})
    ctl.set_php_settings({"memory_limit": "1G"})
    assert "memory_limit = 1G" in (ctl.php.conf_d("8.3") / "99-pipanel.ini").read_text()


def test_setup_stops_when_root_step_fails(ctl, runner):
    runner.on(["pkexec"], Result(126, "", "dibatalkan"))
    steps = ctl.setup()
    assert len(steps) == 1 and not steps[0][1].ok


def test_setup_full_flow(ctl, runner, tmp_path, monkeypatch):
    monkeypatch.setattr(type(ctl.services), "failures", lambda self, names, **k: {})   # no real services here
    import hashlib
    phar = b"P"
    ctl.fetch = lambda u: phar if u.endswith(".phar") else (hashlib.sha256(phar).hexdigest() + " x").encode()
    root = tmp_path / "caroot"; root.mkdir(); (root / "rootCA.pem").write_text("pem")
    runner.on(["mkcert", "-CAROOT"], Result(0, str(root)))
    steps = ctl.setup()
    names = [n for n, _ in steps]
    assert names == ["Packages and system settings", "mkcert CA (browser)", "mkcert CA (system)",
                     "PHP CLI + Composer", "Start services"]
    assert all(r.ok for _, r in steps)
    assert (ctl.paths.bin / "composer").exists() and (ctl.home / ".bashrc").exists()
    assert runner.ran("pkexec", str(ctl.priv.helper), "trust-ca", str(root / "rootCA.pem"))


def test_fix_dispatch(ctl, runner):
    assert not ctl.fix("bogus").ok
    ctl.fix("fix_system_services")
    assert runner.ran("pkexec", str(ctl.priv.helper), "disable-system-services")
    ctl.fix("install_php:8.2")
    assert runner.ran("pkexec", str(ctl.priv.helper), "install-php", "8.2")


def test_share_writes_unit_and_restarts(ctl, tmp_path, runner, paths, monkeypatch):
    mkproj(tmp_path / "proj", "blog")
    ctl.park(tmp_path / "proj")
    ctl.ngrok.bin.write_text("")
    cfg = ctl.ngrok.config_files[0]
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("authtoken: 2abcDEF_1234567890\n")
    from pipanel.core.ngrok import fingerprint
    ctl.cfg.ngrok_verified = fingerprint("2abcDEF_1234567890")
    monkeypatch.setattr(type(ctl.services), "failures", lambda self, names, **k: {})
    ctl.share("blog")
    assert "--host-header=blog.test" in (paths.systemd_user / "pipanel-ngrok.service").read_text()
    assert ["systemctl", "--user", "restart", "pipanel-ngrok.service"] in runner.calls
    ctl.stop_share()
    assert runner.calls[-1][2] == "stop"
    with pytest.raises(ControllerError):
        ctl.share("ghost")


def test_new_laravel_with_mysql_env(ctl, tmp_path, runner, monkeypatch):
    parent = tmp_path / "proj"; parent.mkdir()
    ctl.park(parent)
    (ctl.paths.bin / "composer").write_text("")
    (ctl.paths.mysql_data / "mysql").mkdir(parents=True)

    def fake(argv, **kw):
        if "create-project" in argv:
            d = parent / argv[argv.index("laravel/laravel") + 1]
            d.mkdir(); (d / ".env.example").write_text("DB_CONNECTION=sqlite\n"); (d / "artisan").write_text("")
        return Result(0, "")
    runner.run = fake
    t = ctl.new_laravel("my-app")
    env = (t / ".env").read_text()
    assert "DB_CONNECTION=mysql" in env and "DB_DATABASE=my_app" in env
    assert ctl.last_notes == []


def test_set_site_docroot_applies_and_validates(ctl, tmp_path, paths):
    proj = mkproj(tmp_path / "proj", "app")
    (proj / "web").mkdir()
    ctl.park(tmp_path / "proj")
    ctl.set_site_docroot("app", "web/")
    assert ctl.cfg.sites["app"].docroot == "web"
    assert f'root "{proj.resolve() / "web"}";' in (paths.nginx_sites / "app.conf").read_text()
    with pytest.raises(ControllerError, match="not a folder inside"):
        ctl.set_site_docroot("app", "../x")
    with pytest.raises(ControllerError):
        ctl.set_site_docroot("app", "/etc")
    ctl.set_site_docroot("app", "")   # back to auto-detect
    assert ctl.cfg.sites["app"].docroot is None
    assert f'root "{proj}";' in (paths.nginx_sites / "app.conf").read_text()


def test_replace_parked_folder(ctl, tmp_path):
    a = mkproj(tmp_path / "old", "a").parent
    b = mkproj(tmp_path / "new", "b").parent
    ctl.park(a)
    ctl.park(tmp_path)   # a second folder, to check ordering is kept
    ctl.replace_parked(str(a), b)
    assert ctl.cfg.parked == [str(b), str(tmp_path)]
    assert "b" in [s.name for s in ctl.sites()] and "a" not in [s.name for s in ctl.sites()]
    with pytest.raises(ControllerError):
        ctl.replace_parked(str(b), tmp_path / "nope")


def test_docroot_survives_config_roundtrip(ctl, tmp_path):
    proj = mkproj(tmp_path / "proj", "app")
    (proj / "web").mkdir()
    ctl.park(tmp_path / "proj")
    ctl.set_site_docroot("app", "web")
    again = Controller(ctl.paths, ctl.runner, home=ctl.home, php=ctl.php)
    assert again.cfg.sites["app"].docroot == "web"


# ---------- database engine ----------
def test_db_engine_default_and_persisted(ctl, paths):
    assert ctl.cfg.db_engine == "mysql" and ctl.db_label == "MySQL 8"
    ctl.set_db_engine("mariadb")
    again = Controller(paths, ctl.runner, home=ctl.home, php=ctl.php)
    assert again.cfg.db_engine == "mariadb" and again.db_label == "MariaDB"


def test_switch_engine_stops_running_one_and_starts_new(ctl, runner, paths, tmp_path):
    # the new engine is installed and initialized -> its unit is written and started
    server = tmp_path / "fake-server"
    server.write_text("")
    ctl.mysql.mysqld = str(server)
    new = MySQL(paths, runner, engine="mariadb", mysqld=str(server))
    (new.datadir / "mysql").mkdir(parents=True)
    runner.on(["systemctl", "--user", "is-active"], Result(0, "active\n"))
    import pipanel.core.controller as c
    orig = c.MySQL
    c.MySQL = lambda *a, **k: MySQL(*a, **{**k, "mysqld": str(server)})
    try:
        steps = ctl.set_db_engine("mariadb")
    finally:
        c.MySQL = orig
    assert [n for n, _ in steps] == ["Stop MySQL 8", "Start MariaDB"]
    assert runner.ran("systemctl", "--user", "stop", "pipanel-mysql.service")
    unit = (paths.systemd_user / "pipanel-mysql.service").read_text()
    assert "--defaults-file=" in unit and "mariadb" in unit and "my.cnf" in unit


def test_switch_engine_not_installed_only_records_choice(ctl, runner):
    runner.on(["systemctl", "--user", "is-active"], Result(3, "inactive\n"))
    assert ctl.set_db_engine("mariadb") == []
    assert ctl.cfg.db_engine == "mariadb" and not ctl.mysql.initialized()
    assert ctl.set_db_engine("mariadb") == []   # same engine again: nothing to do


def test_switch_engine_rejects_unknown(ctl):
    with pytest.raises(ControllerError, match="unknown database engine"):
        ctl.set_db_engine("oracle")


def test_install_database_calls_helper_with_engine(ctl, runner):
    ctl.set_db_engine("mariadb")
    runner.on(["pkexec"], Result(126, "", "cancelled"))
    steps = ctl.install_database()
    assert steps[0][0] == "MariaDB packages + AppArmor" and not steps[0][1].ok and len(steps) == 1
    assert runner.ran("pkexec", str(ctl.priv.helper), "install-database", "mariadb")


# ---------- starting services, ports ----------
def test_site_url_includes_a_non_default_port(ctl, tmp_path):
    mkproj(tmp_path / "proj", "app")
    ctl.park(tmp_path / "proj")
    s = ctl.site("app")
    assert ctl.site_url(s) == "http://app.test"
    ctl.set_ports(8080, 8443)
    assert ctl.site_url(ctl.site("app")) == "http://app.test:8080"
    from pipanel.core.sites import Site
    secure = Site(s.name, s.path, s.docroot, s.kind, s.php, True)
    assert ctl.site_url(secure) == "https://app.test:8443"


def test_set_ports_validates_and_rewrites_nginx(ctl, tmp_path, paths):
    mkproj(tmp_path / "proj", "app")
    ctl.park(tmp_path / "proj")
    for bad in ((0, 443), (80, 70000), (8080, 8080)):
        with pytest.raises(ControllerError):
            ctl.set_ports(*bad)
    ctl.set_ports(8080, 8443)
    assert "listen 8080;" in (paths.nginx_sites / "app.conf").read_text()
    assert "listen 8080 default_server;" in (paths.nginx_dir / "nginx.conf").read_text()
    assert ctl.cfg.http_port == 8080


def test_start_all_reports_why_nginx_did_not_start(ctl, runner, monkeypatch):
    runner.on(["systemctl", "--user", "is-active"], Result(3, "inactive\nactive\nactive\n"))
    runner.on(["journalctl"], Result(0, "Starting pipanel-nginx.service\nnginx: [emerg] bind() to 0.0.0.0:80 failed "
                                        "(98: Address already in use)\n"))
    runner.on(["ss"], Result(0, "LISTEN 0 4096 127.0.0.1:80 0.0.0.0:*\n"))
    runner.on(["docker"], Result(0, "ddev-router\t127.0.0.1:80->80/tcp, 127.0.0.1:443->443/tcp\n"))
    monkeypatch.setattr(type(ctl.services), "failures", lambda self, names, **k: {"nginx": "nginx: [emerg] bind() to 0.0.0.0:80 failed (98: Address already in use)"})
    r = ctl.start_all()
    assert not r.ok
    assert "Nginx did not start" in r.text and "Address already in use" in r.text
    assert "Port 80 is used by the Docker container ddev-router (DDEV)" in r.text
    assert "ddev poweroff" in r.text and "Settings" in r.text


def test_start_all_ok_when_everything_stays_up(ctl, runner, monkeypatch):
    monkeypatch.setattr(type(ctl.services), "failures", lambda self, names, **k: {})
    assert ctl.start_all().ok


def test_new_laravel_runs_migrations_after_configuring_the_database(ctl, tmp_path, runner):
    parent = tmp_path / "proj"; parent.mkdir()
    ctl.park(parent)
    (ctl.paths.bin / "composer").write_text("")
    (ctl.paths.bin / "composer.phar").write_text("")
    (ctl.paths.mysql_data / "mysql").mkdir(parents=True)
    seen = []

    def fake(argv, **kw):
        seen.append(list(argv))
        if "create-project" in argv:
            d = parent / argv[argv.index("laravel/laravel") + 1]
            d.mkdir(); (d / ".env.example").write_text("DB_CONNECTION=sqlite\n"); (d / "artisan").write_text("")
        return Result(0, "")
    runner.run = fake
    ctl.new_laravel("blog2")
    migrate = [c for c in seen if "migrate" in c]
    assert migrate and migrate[0][1:] == ["artisan", "migrate", "--force", "--no-interaction"]
    assert seen.index(migrate[0]) > max(i for i, c in enumerate(seen) if "create-project" in c)   # after the project exists


def test_new_laravel_reports_a_failed_migration_but_keeps_the_project(ctl, tmp_path, runner):
    parent = tmp_path / "proj"; parent.mkdir()
    ctl.park(parent)
    (ctl.paths.bin / "composer").write_text("")
    (ctl.paths.mysql_data / "mysql").mkdir(parents=True)

    def fake(argv, **kw):
        if "create-project" in argv:
            d = parent / argv[argv.index("laravel/laravel") + 1]
            d.mkdir(); (d / ".env.example").write_text("DB_CONNECTION=sqlite\n"); (d / "artisan").write_text("")
        if "migrate" in argv:
            return Result(1, "", "SQLSTATE[HY000] [2002] Connection refused")
        return Result(0, "")
    runner.run = fake
    target = ctl.new_laravel("blog3")
    assert target.is_dir() and any("migrate" in n and "Connection refused" in n for n in ctl.last_notes)


def test_every_installed_php_version_gets_a_unit(ctl, tmp_path, paths):
    ctl.services.sync(ctl.cfg, [])
    assert (paths.systemd_user / "pipanel-php8.2.service").exists() and (paths.systemd_user / "pipanel-php8.3.service").exists()
