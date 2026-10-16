from pipanel.core.certs import Certs
from pipanel.core.config import Config
from pipanel.core.doctor import Doctor, parse_listeners, OK, WARN, FAIL
from pipanel.core.php import PHP
from pipanel.core.privileged import Privileged
from pipanel.core.runner import Result
from pipanel.core.services import Services

SS = '''LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("apache2",pid=1,fd=4))
LISTEN 0 4096 127.0.0.1:5354 0.0.0.0:*
LISTEN 0 511 [::]:443 [::]:* users:(("nginx",pid=2,fd=6))
'''


def test_parse_listeners():
    assert parse_listeners(SS) == {80: "apache2", 5354: "", 443: "nginx"}


def make(paths, runner, tmp_path, *, query=lambda *a, **k: ["127.0.0.1"], sysres=lambda h: True):
    sbin = tmp_path / "sbin"; sbin.mkdir(exist_ok=True)
    (sbin / "php-fpm8.3").write_text("")
    php = PHP(paths, etc_php=tmp_path / "etc", sbin=sbin, usr_bin=tmp_path)
    svc = Services(paths, runner, php, nginx_version="1.24.0")
    dropin = tmp_path / "pipanel.conf"
    dropin.write_text("x")
    return Doctor(paths, runner, Config(), svc, php, Certs(paths, runner, system_ca=tmp_path / "ca.crt"),
                  Privileged(runner, helper=tmp_path / "helper"), resolved_dropin=dropin,
                  sysctl_file=tmp_path / "sysctl", query=query, sys_resolves=sysres)


def test_port_conflict_detected(paths, runner, tmp_path):
    runner.on(["ss"], Result(0, SS))
    runner.on(["systemctl", "--user", "is-active"], Result(3, "inactive\n"))
    c = make(paths, runner, tmp_path).ports()
    assert c.status == FAIL and "apache2" in c.detail


def test_ports_ok_when_ours(paths, runner, tmp_path):
    runner.on(["ss"], Result(0, SS.replace("apache2", "nginx")))
    runner.on(["systemctl", "--user", "is-active"], Result(0, "active\n"))
    (paths.systemd_user / "pipanel-nginx.service").write_text("")
    assert make(paths, runner, tmp_path).ports().status == OK


def test_system_service_active_warns(paths, runner, tmp_path):
    runner.on(["systemctl", "is-active", "nginx.service"], Result(0, "active\n"))
    c = make(paths, runner, tmp_path).system_services()
    assert c.status == WARN and c.fix == "fix_system_services" and "nginx" in c.detail


def test_dns_levels(paths, runner, tmp_path):
    assert make(paths, runner, tmp_path).dns().status == OK
    assert make(paths, runner, tmp_path, query=lambda *a, **k: []).dns().status == FAIL
    assert make(paths, runner, tmp_path, sysres=lambda h: False).dns().status == FAIL


def test_ca_and_helper(paths, runner, tmp_path):
    d = make(paths, runner, tmp_path)
    runner.on(["mkcert", "-CAROOT"], Result(0, str(tmp_path / "root")))
    assert d.ca().status == WARN and d.ca().fix == "install_ca"
    (tmp_path / "root").mkdir(); (tmp_path / "root" / "rootCA.pem").write_text("x")
    assert d.ca().status == WARN and "not trusted" in d.ca().title
    (tmp_path / "ca.crt").write_text("x")
    assert d.ca().status == OK
    assert d.helper().status == FAIL


def test_missing_php_and_socket(paths, runner, tmp_path):
    d = make(paths, runner, tmp_path)
    checks = {c.id: c for c in d.sockets({"8.2"})}
    assert checks["php8.2"].fix == "install_php:8.2" and checks["php8.3"].fix == "start_all"


def test_missing_mkcert_is_only_a_warning_and_does_not_make_the_base_check_fail(paths, runner, tmp_path, monkeypatch):
    d = make(paths, runner, tmp_path)
    monkeypatch.setattr("pipanel.core.doctor.find_bin", lambda n: str(tmp_path / n) if n != "mkcert" else "/nonexistent/mkcert")
    for n in ("nginx", "dnsmasq"):
        (tmp_path / n).write_text("")
    assert d.packages().status == OK                       # mkcert is not part of the required packages
    monkeypatch.setattr(d.certs, "available", lambda: False)
    ids = {c.id: c for c in d.run_all()}
    assert ids["mkcert"].status == WARN and "http://" in ids["mkcert"].title and "ca" not in ids
    monkeypatch.setattr(d.certs, "available", lambda: True)
    assert "mkcert" not in {c.id for c in d.run_all()} and "ca" in {c.id for c in d.run_all()}
