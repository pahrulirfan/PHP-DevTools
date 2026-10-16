import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parent.parent / "packaging" / "pipanel-helper"


def run_helper(tmp_path, *args, uid=None):
    env = {**os.environ, "PIPANEL_HELPER_DRYRUN": "1", "PIPANEL_HELPER_ROOT": str(tmp_path)}
    if uid is not None:
        env["PKEXEC_UID"] = str(uid)
    p = subprocess.run([sys.executable, str(HELPER), *args], capture_output=True, text=True, env=env)
    cmds = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    return p, cmds


def argvs(cmds):
    return [c for c in cmds if isinstance(c, list)]


def test_unknown_and_empty_subcommand(tmp_path):
    assert run_helper(tmp_path)[0].returncode == 2
    assert run_helper(tmp_path, "rm-rf")[0].returncode == 2


@pytest.mark.parametrize("bad", ["7.4", "8", "8.3; ls", "../x", "8.10", ""])
def test_install_php_rejects_bad_version(tmp_path, bad):
    p, cmds = run_helper(tmp_path, "install-php", bad)
    assert p.returncode == 2 and not cmds


def test_install_php_commands(tmp_path):
    p, cmds = run_helper(tmp_path, "install-php", "8.2")
    assert p.returncode == 0
    inst = [c for c in argvs(cmds) if c[:2] == ["apt-get", "install"]][0]
    assert "php8.2-fpm" in inst and "php8.2-mbstring" in inst
    assert ["systemctl", "disable", "--now", "php8.2-fpm.service"] in argvs(cmds)


@pytest.mark.parametrize("bad", ["x y", "a;b", "../x", "", "UP"])
def test_install_ext_rejects_bad_name(tmp_path, bad):
    assert run_helper(tmp_path, "install-ext", "8.3", bad)[0].returncode == 2


def test_setup_writes_system_files(tmp_path):
    p, cmds = run_helper(tmp_path, "setup", "8.3")
    assert p.returncode == 0, p.stderr
    assert (tmp_path / "etc/sysctl.d/60-pipanel.conf").read_text() == "net.ipv4.ip_unprivileged_port_start=80\n"
    r = (tmp_path / "etc/systemd/resolved.conf.d/pipanel.conf").read_text()
    assert "DNS=127.0.0.1:5354" in r and "Domains=~test" in r
    a = argvs(cmds)
    assert ["add-apt-repository", "-y", "ppa:ondrej/php"] in a
    assert any(c[:2] == ["apt-get", "install"] and "mkcert" in c and "dnsmasq-base" in c for c in a)
    assert ["systemctl", "disable", "--now", "nginx.service"] in a


def test_setup_requires_pkexec_when_not_dry(tmp_path):
    env = {**os.environ}
    env.pop("PIPANEL_HELPER_DRYRUN", None)
    p = subprocess.run([sys.executable, str(HELPER), "setup"], capture_output=True, text=True, env=env)
    assert p.returncode == 2 and "root" in p.stderr


def test_trust_ca_only_users_mkcert_root(tmp_path, monkeypatch):
    import pwd
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    # an arbitrary path is rejected
    evil = tmp_path / "evil.pem"
    evil.write_text("-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n")
    p, _ = run_helper(tmp_path, "trust-ca", str(evil))
    assert p.returncode == 2 and "only" in p.stderr
    p, _ = run_helper(tmp_path, "trust-ca", "/etc/shadow")
    assert p.returncode == 2
    p, _ = run_helper(tmp_path, "trust-ca")
    assert p.returncode == 2
    assert not (tmp_path / "usr").exists()


@pytest.mark.parametrize("engine,pkgs,profile", [
    ("mysql", ["mysql-server-core-8.0", "mysql-client-core-8.0"], "usr.sbin.mysqld"),
    ("mariadb", ["mariadb-server-core", "mariadb-client-core"], "usr.sbin.mariadbd"),
])
def test_install_database_per_engine(tmp_path, engine, pkgs, profile):
    p, cmds = run_helper(tmp_path, "install-database", engine)
    assert p.returncode == 0, p.stderr
    a = argvs(cmds)
    inst = [c for c in a if c[:2] == ["apt-get", "install"]][0]
    assert all(pkg in inst for pkg in pkgs)
    assert ["systemctl", "disable", "--now", "mariadb.service"] in a   # no system DB service left running
    rules = (tmp_path / "etc/apparmor.d/local" / profile).read_text()
    assert f"/.local/share/pipanel/{engine}/** rwk," in rules and f"/.config/pipanel/{engine}/** r," in rules
    assert ["apparmor_parser", "-r", f"/etc/apparmor.d/{profile}"] in a


@pytest.mark.parametrize("bad", ["", "postgres", "mysql;ls", "../mariadb"])
def test_install_database_rejects_unknown_engine(tmp_path, bad):
    p, cmds = run_helper(tmp_path, "install-database", *([bad] if bad else []))
    assert p.returncode == 2 and not cmds


def test_setup_reinstalls_a_package_whose_file_was_removed_by_another_package(tmp_path):
    # dpkg says "installed" but /usr/bin/mkcert is gone (ddev removed it): apt install alone would do nothing
    p, cmds = run_helper(tmp_path, "setup", "8.3")
    assert p.returncode == 0, p.stderr
    re = [c for c in argvs(cmds) if c[:3] == ["apt-get", "install", "--reinstall"]]
    assert re and {"mkcert", "nginx", "dnsmasq-base"} <= set(re[0])


def test_setup_does_not_reinstall_when_the_files_exist(tmp_path):
    for f in ("usr/sbin/nginx", "usr/sbin/dnsmasq", "usr/bin/mkcert"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("")
    p, cmds = run_helper(tmp_path, "setup", "8.3")
    assert p.returncode == 0, p.stderr
    assert not [c for c in argvs(cmds) if "--reinstall" in c]
