import pytest

from pipanel.core.config import Config, SiteOverride
from pipanel.core.php import PHP
from pipanel.core.runner import Result
from pipanel.core.services import Services, ServiceError, unit_name
from pipanel.core.sites import Site


@pytest.fixture
def svc(paths, runner, tmp_path):
    sbin = tmp_path / "sbin"
    sbin.mkdir()
    (sbin / "php-fpm8.3").write_text("")
    php = PHP(paths, etc_php=tmp_path / "etc", sbin=sbin, usr_bin=tmp_path)
    return Services(paths, runner, php, nginx_version="1.24.0", nginx_bin=str(tmp_path / "no-nginx"))


def site(name, tmp_path, php="8.3", https=False):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return Site(name, d, d, "php", php, https)


def test_sync_writes_everything(svc, paths, tmp_path, runner):
    cfg = Config()
    rep = svc.sync(cfg, [site("blog", tmp_path)])
    assert rep.changed
    assert (paths.nginx_dir / "nginx.conf").exists()
    assert "server_name blog.test;" in (paths.nginx_sites / "blog.conf").read_text()
    assert (paths.php_dir("8.3") / "php-fpm.conf").exists()
    assert (paths.dns_dir / "dnsmasq.conf").exists()
    for u in ("nginx", "dns", "php8.3"):
        assert (paths.systemd_user / unit_name(u)).exists()
    assert runner.ran("systemctl", "--user", "daemon-reload")
    runner.calls.clear()
    assert not svc.sync(cfg, [site("blog", tmp_path)]).changed   # idempoten
    assert not runner.ran("systemctl", "--user", "daemon-reload")


def test_unit_contents(svc, paths, tmp_path):
    svc.sync(Config(), [])
    nginx = (paths.systemd_user / unit_name("nginx")).read_text()
    assert f"-p {paths.nginx_dir} -c {paths.nginx_dir}/nginx.conf -e stderr" in nginx
    assert "ExecReload=" in nginx and "-s reload" in nginx
    fpm = (paths.systemd_user / unit_name("php8.3")).read_text()
    assert "--nodaemonize" in fpm and f"PHP_INI_SCAN_DIR={paths.php_dir('8.3')}/conf.d" in fpm
    assert "ExecStartPre=/bin/mkdir -p" in fpm


def test_stale_site_removed(svc, paths, tmp_path):
    svc.sync(Config(), [site("a", tmp_path), site("b", tmp_path)])
    svc.sync(Config(), [site("a", tmp_path)])
    assert [f.name for f in paths.nginx_sites.glob("*.conf")] == ["a.conf"]


def test_https_without_cert_downgrades_with_warning(svc, paths, tmp_path):
    rep = svc.sync(Config(), [site("blog", tmp_path, https=True)])
    assert any("certificate" in w for w in rep.warnings)
    assert "443" not in (paths.nginx_sites / "blog.conf").read_text()
    (paths.certs / "blog.test.pem").write_text("x")
    svc.sync(Config(), [site("blog", tmp_path, https=True)])
    assert "listen 443 ssl" in (paths.nginx_sites / "blog.conf").read_text()


def test_missing_php_version_warns(svc, tmp_path):
    rep = svc.sync(Config(), [site("old", tmp_path, php="8.2")])
    assert any("PHP 8.2 is not installed" in w for w in rep.warnings)


def test_nginx_test_failure_rolls_back(svc, paths, tmp_path, runner):
    svc.sync(Config(), [site("a", tmp_path)])
    before = (paths.nginx_sites / "a.conf").read_text()
    fake = tmp_path / "fake-nginx"
    fake.write_text("")
    svc.nginx_bin = str(fake)
    runner.on([str(fake), "-p"], Result(1, "", "nginx: [emerg] boom"))
    with pytest.raises(ServiceError, match="boom"):
        svc.sync(Config(), [site("a", tmp_path), site("b", tmp_path)])
    assert [f.name for f in paths.nginx_sites.glob("*.conf")] == ["a.conf"]
    assert (paths.nginx_sites / "a.conf").read_text() == before


def test_status_parsing(svc, paths, runner):
    svc.sync(Config(), [])
    runner.on(["systemctl", "--user", "is-active"], Result(3, "active\ninactive\nfailed\n"))
    assert svc.status(["nginx", "dns", "php8.3"]) == {"nginx": "active", "dns": "inactive", "php8.3": "failed"}
    runner.responses.clear()
    runner.on(["systemctl", "--user", "is-active"], Result(3, "inactive\n"))
    assert svc.status(["mysql"]) == {"mysql": "not-found"}


def test_control_and_reload_fallback(svc, runner):
    svc.start("nginx")
    assert runner.calls[-1] == ["systemctl", "--user", "start", "pipanel-nginx.service"]
    runner.on(["systemctl", "--user", "reload"], Result(1, "", "no reload"))
    svc.reload("nginx")
    assert runner.calls[-1][2] == "restart"


def test_start_all_enables_when_autostart(svc, runner):
    svc.start_all(Config(autostart_services=True))
    assert runner.ran("systemctl", "--user", "enable", "pipanel-nginx.service", "pipanel-dns.service")


def test_unit_prefix_can_be_overridden_for_real_tests(monkeypatch):
    import importlib
    import pipanel.core.services as s
    monkeypatch.setenv("PIPANEL_UNIT_PREFIX", "pipanel-e2e")
    s2 = importlib.reload(s)
    try:
        assert s2.unit_name("nginx") == "pipanel-e2e-nginx.service"
    finally:
        monkeypatch.delenv("PIPANEL_UNIT_PREFIX")
        importlib.reload(s)
    assert s.unit_name("nginx") == "pipanel-nginx.service"


def test_real_test_scripts_never_use_the_real_unit_names():
    from pathlib import Path
    for f in Path(__file__).resolve().parent.parent.glob("tools/e2e_*.py"):
        text = f.read_text()
        if "systemctl" in text or "unit_name" in text:
            assert 'PIPANEL_UNIT_PREFIX", "pipanel-e2e"' in text, f.name
        assert "pipanel-nginx" not in text and "pipanel-mysql" not in text and "pipanel-dns" not in text, f.name
