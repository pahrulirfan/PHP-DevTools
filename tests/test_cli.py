import io

import pytest

from pipanel import cli
from pipanel.core.controller import Controller
from pipanel.core.php import PHP
from pipanel.core.privileged import Privileged
from pipanel.core.runner import Result


@pytest.fixture
def ctl(paths, runner, tmp_path, no_system_db):
    sbin = tmp_path / "sbin"; sbin.mkdir()
    for v in ("8.2", "8.3"):
        (sbin / f"php-fpm{v}").write_text("")
    c = Controller(paths, runner, home=tmp_path / "home", php=PHP(paths, etc_php=tmp_path / "etc", sbin=sbin,
                                                                 usr_bin=tmp_path),
                   privileged=Privileged(runner, tmp_path / "helper"), fetch=lambda u: b"")
    c.services._nginx_version = "1.24.0"
    c.services.nginx_bin = str(tmp_path / "no-nginx")
    proj = tmp_path / "proj"
    for n in ("blog", "shop"):
        (proj / n / "src").mkdir(parents=True)
    c.park(proj)
    return c


def run(ctl, *argv):
    out = io.StringIO()
    code = cli.main(list(argv), ctl, out)
    return code, out.getvalue()


def test_is_cli():
    assert cli.is_cli(["status"]) and cli.is_cli(["--help"])
    assert not cli.is_cli([]) and not cli.is_cli(["--minimized"])


def test_sites_and_which(ctl, tmp_path, monkeypatch):
    code, out = run(ctl, "sites")
    assert code == 0 and "http://blog.test" in out and "http://shop.test" in out
    monkeypatch.chdir(tmp_path / "proj" / "blog" / "src")
    assert "blog: http://blog.test  PHP 8.3" in run(ctl, "which")[1]
    monkeypatch.chdir(tmp_path)
    assert "default PHP 8.3" in run(ctl, "which")[1]


def test_isolate_uses_the_current_folder(ctl, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path / "proj" / "shop")
    code, out = run(ctl, "isolate", "8.2")
    assert code == 0 and ctl.site("shop").php == "8.2"
    assert "8.2" in (ctl.paths.config / "php" / "sites.map").read_text()


def test_errors_are_messages(ctl, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert run(ctl, "isolate", "8.2")[0] == 1
    assert "not inside a site folder" in capsys.readouterr().err
    assert run(ctl, "isolate", "9.9", "blog")[0] == 1


def test_link_and_proxy(ctl, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere" / "Api"
    other.mkdir(parents=True)
    monkeypatch.chdir(other)
    code, out = run(ctl, "link")
    assert code == 0 and ctl.site("api").linked and "http://api.test" in out
    code, out = run(ctl, "proxy", "next", "3000")
    assert code == 0 and "http://next.test -> http://127.0.0.1:3000" in out
    assert run(ctl, "unproxy", "next")[0] == 0 and not ctl.proxies()


def test_status_marks_unused_php(ctl, runner):
    runner.on(["systemctl", "--user", "is-active"], Result(3, "active\nactive\ninactive\nactive\n"))
    out = run(ctl, "status")[1]
    assert "Nginx" in out and "PHP-FPM 8.2" in out and "no site uses it" in out


@pytest.mark.posix
def test_launcher_script(ctl):
    f = ctl.install_cli_launcher("/opt/venv/bin/python")
    assert f.read_text() == '#!/bin/sh\nexec "/opt/venv/bin/python" -m pipanel "$@"\n' and f.stat().st_mode & 0o100


def test_stop_a_tool_is_remembered(ctl, runner):
    code, _ = run(ctl, "stop", "mail")
    assert code == 0 and ctl.cfg.stopped_tools == ["mail"]
    run(ctl, "start", "mail")
    assert ctl.cfg.stopped_tools == []
