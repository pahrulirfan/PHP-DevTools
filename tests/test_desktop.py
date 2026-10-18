"""Windows and macOS code paths, checked on any machine with the platform detection forced."""
import hashlib
import io
import json
import zipfile

import pytest

from pipanel.core import platform
from pipanel.core.runner import Result


def zipped(files: dict, top: str = "") -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, data in files.items():
            z.writestr(top + name, data)
    return b.getvalue()


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setenv("PIPANEL_PLATFORM", "windows")


@pytest.fixture
def macos(monkeypatch):
    monkeypatch.setenv("PIPANEL_PLATFORM", "macos")


# ---------- Windows: PHP ----------
def php_zip():
    return zipped({"php.exe": b"MZ", "php-cgi.exe": b"MZ", "ext/php_mbstring.dll": b"x", "ext/php_curl.dll": b"x",
                   "ext/php_opcache.dll": b"x", "ext/php_pdo_mysql.dll": b"x", "ext/php_openssl.dll": b"x"})


def releases(data: bytes, v="8.4", sha=None):
    return json.dumps({v: {"version": f"{v}.1", "ts-vs17-x64": {"zip": {"path": "ts.zip", "sha256": "0"}},
                           "nts-vs17-x64": {"zip": {"path": f"php-{v}.1-nts-Win32-vs17-x64.zip",
                                                    "sha256": sha or hashlib.sha256(data).hexdigest()}}}}).encode()


def test_windows_php_install_and_extensions(windows, paths):
    from pipanel.core.php import PHPError
    from pipanel.core.php_win import RELEASES, WindowsPHP, base_port
    data = php_zip()
    files = {RELEASES: releases(data), "https://downloads.php.net/~windows/releases/php-8.4.1-nts-Win32-vs17-x64.zip": data}
    php = WindowsPHP(paths)
    php.install("8.4", files.__getitem__)
    assert php.installed() == ["8.4"] and php.cli_bin("8.4").name == "php.exe"
    php.ensure_conf_d("8.4")
    on = dict(php.extensions("8.4"))
    assert on["mbstring"] and on["curl"] and on["opcache"] and on["pdo_mysql"]
    assert "zend_extension=opcache" in (php.conf_d("8.4") / "20-opcache.ini").read_text()
    assert f'extension_dir = "{php.ext_dir("8.4").as_posix()}"' in (php.conf_d("8.4") / "00-pipanel-base.ini").read_text()
    php.set_extension("8.4", "curl", False)
    assert not dict(php.extensions("8.4"))["curl"]
    with pytest.raises(PHPError):
        php.set_extension("8.4", "imagick", True)
    assert php.xdebug_state("8.4") == "missing"
    assert base_port("8.4") == 9840 and php.upstream("8.4") == "php84"
    assert php.upstream_servers("8.4") == ["127.0.0.1:9840", "127.0.0.1:9841", "127.0.0.1:9842", "127.0.0.1:9843"]
    argv = php.server_argv("8.4")
    assert argv[-7:] == ["_fcgi", "--php", str(php.fpm_bin("8.4")), "--port", "9840", "--workers", "4"]
    files[RELEASES] = releases(data, sha="0" * 64)
    with pytest.raises(PHPError, match="checksum"):
        php.install("8.4", files.__getitem__)


def test_windows_extension_downloads(windows, paths):
    from pipanel.core.php_win import PECL, XDEBUG_PAGE, WindowsPHP
    php = WindowsPHP(paths)
    php.ext_dir("8.3").mkdir(parents=True)
    pecl_zip = zipped({"php_redis.dll": b"REDIS", "LICENSE": b""})
    files = {
        XDEBUG_PAGE: b'<a href="/files/php_xdebug-3.5.3-8.3-nts-vs16-x86_64.dll">',
        "https://xdebug.org/files/php_xdebug-3.5.3-8.3-nts-vs16-x86_64.dll": b"XDEBUG",
        PECL + "redis/": b'<a href="6.2.0/">6.2.0/</a><a href="6.3.0/">6.3.0/</a>',
        PECL + "redis/6.3.0/": b'php_redis-6.3.0-8.3-nts-vs16-x64.zip php_redis-6.3.0-8.3-ts-vs16-x64.zip',
        PECL + "redis/6.3.0/php_redis-6.3.0-8.3-nts-vs16-x64.zip": pecl_zip,
    }
    php.install_extension("8.3", "xdebug", files.__getitem__)
    php.install_extension("8.3", "redis", files.__getitem__)
    assert (php.ext_dir("8.3") / "php_xdebug.dll").read_bytes() == b"XDEBUG"
    assert (php.ext_dir("8.3") / "php_redis.dll").read_bytes() == b"REDIS"
    assert php.xdebug_state("8.3") == "off"
    php.set_xdebug("8.3", True)
    assert "zend_extension=xdebug" in (php.conf_d("8.3") / "20-xdebug.ini").read_text()


def test_windows_cli_wrappers(windows, paths):
    from pipanel.core.php_win import WindowsPHP
    php = WindowsPHP(paths)
    php.write_cli_wrapper("8.4")
    text = (paths.bin / "php.cmd").read_text()
    assert "_run php %*" in text and php.cli_version() == "8.4"
    assert "_run composer %*" in (paths.bin / "composer.cmd").read_text()


# ---------- Windows: config, services, stack ----------
@pytest.fixture
def win_ctl(windows, paths, tmp_path):
    from pipanel.core.controller import Controller
    from pipanel.core.supervisor import AgentSupervisor
    from pipanel.core.privileged import WindowsPrivileged
    from tests.conftest import FakeRunner
    runner = FakeRunner()
    ctl = Controller(paths, runner, home=tmp_path / "home", fetch=lambda u: b"",
                     privileged=WindowsPrivileged(runner, tmp_path / "helper.ps1"))
    ctl.services.sup = AgentSupervisor(paths, start_agent=False)
    ctl.services._nginx_version = "1.30.6"
    root = ctl.php.root / "8.4"
    (root / "ext").mkdir(parents=True)
    (root / "php-cgi.exe").write_text(""), (root / "php.exe").write_text("")
    return ctl


def test_windows_sync(win_ctl, paths, tmp_path):
    ctl = win_ctl
    assert ctl.cfg.dns_port == 53 and ctl.cfg.default_php == "8.4"
    proj = tmp_path / "My Projects" / "blog"
    (proj / "public").mkdir(parents=True), (proj / "artisan").write_text("")
    ctl.park(proj.parent)
    ctl.apply()
    conf = (paths.nginx_dir / "nginx.conf").read_text()
    assert "upstream php84 {" in conf and "server 127.0.0.1:9840;" in conf and "listen 127.0.0.1:80 default_server" in conf
    site = (paths.nginx_sites / "blog.conf").read_text()
    assert "fastcgi_pass php84;" in site and f'root "{(proj / "public").as_posix()}";' in site
    assert "listen 127.0.0.1:80;" in site
    assert not (paths.php_dir("8.4") / "php-fpm.conf").exists() and not (paths.dns_dir / "dnsmasq.conf").exists()
    units = paths.config / "units"
    php_unit = json.loads((units / "php8.4.json").read_text())
    assert "_fcgi" in php_unit["argv"] and php_unit["reload"] is None
    dns_unit = json.loads((units / "dns.json").read_text())
    assert dns_unit["argv"][-5:] == ["_dns", "--port", "53", "--tld", "test"]
    watch = json.loads((units / "watch.folders").read_text())
    assert watch["folders"] == [str(proj.parent)] and watch["argv"][-2:] == ["sync", "--settle"]
    assert ctl.needs_setup()


def test_windows_stack_downloads(windows, paths, runner, monkeypatch):
    from pipanel.core import stack
    monkeypatch.setattr(stack, "machine", lambda: "amd64")
    st = stack.WindowsStack(paths, runner)
    nginx = zipped({"nginx.exe": b"MZ", "conf/mime.types": b"", "conf/fastcgi_params": b""}, "nginx-1.30.6/")
    maria = zipped({"bin/mariadbd.exe": b"MZ", "bin/mariadb.exe": b"MZ"}, "mariadb-11.4.13-winx64/")
    redis = zipped({"redis-server.exe": b"MZ"}, "Redis-8.10.2-Windows-x64-msys2/")
    files = {
        stack.NGINX_DOWNLOAD: b'Mainline <a href="/download/nginx-1.31.0.zip"> Stable version <a href="/download/nginx-1.30.6.zip">',
        "https://nginx.org/download/nginx-1.30.6.zip": nginx,
        "https://github.com/FiloSottile/mkcert/releases/download/v1.4.4/mkcert-v1.4.4-windows-amd64.exe": b"MZ",
        f"{stack.MARIADB_API}11.4/latest/": json.dumps({"releases": {"11.4.13": {"files": [
            {"file_name": "mariadb-11.4.13-winx64-debugsymbols.zip", "file_download_url": "http://x/debug.zip"},
            {"file_name": "mariadb-11.4.13-winx64.zip", "file_download_url": "http://x/m.zip",
             "checksum": {"sha256sum": hashlib.sha256(maria).hexdigest()}}]}}}).encode(),
        "https://x/m.zip": maria,
        stack.REDIS_WINDOWS_PAGE: b'<a href="/redis-windows/redis-windows/releases/tag/8.10.2">8.10.2</a>',
        stack.REDIS_WINDOWS_ASSET.format(tag="8.10.2"): redis,
    }
    assert st.install_base(files.__getitem__).ok
    assert st.ready() and (paths.bin / "mkcert.exe").exists()
    assert st.etc_nginx.endswith("nginx/conf")
    assert st.install_database("mariadb", files.__getitem__).ok
    assert (st.root / "mariadb" / "bin" / "mariadbd.exe").exists()
    assert st.db("mariadb")["init"].endswith("mariadb-install-db.exe")
    assert st.install_redis(files.__getitem__).ok and (st.root / "redis" / "redis-server.exe").exists()
    files["https://x/m.zip"] = b"PK broken"
    assert not st.install_database("mariadb", files.__getitem__).ok


def test_windows_mysql_client_and_init(windows, paths, runner):
    from pipanel.core.mysql import MySQL
    m = MySQL(paths, runner, engine="mariadb", mysqld="C:/s/mariadbd.exe", client="C:/s/mariadb.exe",
              init="C:/s/mariadb-install-db.exe", basedir="C:/s")
    m.initialize()
    cnf = m.cnf.read_text()
    assert "socket=" not in cnf and "basedir=C:/s" in cnf and "lc-messages-dir" not in cnf
    client = m.client_cnf.read_text()
    assert "host=127.0.0.1" in client and "protocol=TCP" in client and "socket" not in client
    init = runner.calls[-1]
    assert init[0].endswith("mariadb-install-db.exe") and "--silent" in init and not any("--defaults-file" in a for a in init)
    assert m._root_hosts() == ("localhost", "127.0.0.1", "::1")


def test_windows_privileged_command(windows, runner, tmp_path):
    from pipanel.core.privileged import WindowsPrivileged
    helper = tmp_path / "pipanel-helper.ps1"
    helper.write_text("")
    priv = WindowsPrivileged(runner, helper)
    assert priv.setup_dns("test").ok
    cmd = runner.calls[-1]
    assert cmd[:3] == ["powershell", "-NoProfile", "-Command"]
    assert "Start-Process powershell -Verb RunAs" in cmd[3] and "'setup-dns'" in cmd[3] and "'test'" in cmd[3]
    priv.dns_ready("test")
    assert runner.calls[-1][0] == "powershell" and "check" in runner.calls[-1]
    assert not WindowsPrivileged(runner, tmp_path / "missing.ps1").setup_dns().ok


def test_run_tool_picks_the_site_version(windows, paths, tmp_path, monkeypatch):
    from pipanel import cli
    from pipanel.core.php_win import WindowsPHP
    monkeypatch.setenv("PIPANEL_HOME", str(paths.config.parent))
    php = WindowsPHP(paths)
    for v in ("8.3", "8.4"):
        (php.root / v).mkdir(parents=True)
        (php.root / v / "php-cgi.exe").write_text("")
    site = tmp_path / "proj" / "legacy"
    site.mkdir(parents=True)
    php.write_site_map([(site, "8.3")])
    seen = []
    monkeypatch.setattr("subprocess.call", lambda cmd, env: seen.append((cmd, env)) or 0)
    from pipanel.core import paths as P
    monkeypatch.setattr(P.Paths, "from_env", classmethod(lambda cls, *a, **k: paths))
    assert cli.run_tool(["php", "-v"], cwd=site) == 0
    assert seen[-1][0] == [str(php.cli_bin("8.3")), "-v"]
    assert seen[-1][1]["PHP_INI_SCAN_DIR"] == str(php.conf_d("8.3"))
    cli.run_tool(["composer", "install"], cwd=tmp_path)
    assert seen[-1][0][0] == str(php.cli_bin("8.3")) or seen[-1][0][0] == str(php.cli_bin("8.4"))
    assert seen[-1][0][1].endswith("composer.phar") and seen[-1][0][2] == "install"


# ---------- macOS ----------
def test_brew_php(macos, paths, tmp_path, runner):
    from pipanel.core.php_mac import TAP, BrewPHP
    prefix = tmp_path / "homebrew"
    for v in ("8.3", "8.4"):
        (prefix / "opt" / f"php@{v}" / "sbin").mkdir(parents=True)
        (prefix / "opt" / f"php@{v}" / "sbin" / "php-fpm").write_text("")
    confd = prefix / "etc" / "php" / "8.4" / "conf.d"
    confd.mkdir(parents=True)
    (confd / "ext-opcache.ini").write_text("zend_extension=opcache\n")
    (confd / "ext-xdebug.ini").write_text("zend_extension=xdebug\n")
    php = BrewPHP(paths, prefix)
    assert php.installed() == ["8.3", "8.4"]
    assert php.server_argv("8.4")[0] == str(prefix / "opt/php@8.4/sbin/php-fpm")
    php.ensure_conf_d("8.4")
    assert dict(php.extensions("8.4")) == {"opcache": True, "xdebug": True}
    php.set_extension("8.4", "xdebug", False)
    assert php.xdebug_state("8.4") == "off"
    php.set_xdebug("8.4", True)
    assert php.xdebug_state("8.4") == "on"
    php.install("8.2", runner)
    assert ["brew", "tap", TAP] in runner.calls and ["brew", "install", f"{TAP}/php@8.2"] in runner.calls
    assert f"{prefix}/opt/php@$v/bin/php" in php.write_cli_wrapper("8.4").read_text()


def test_mac_privileged(macos, runner, tmp_path):
    from pipanel.core.privileged import MacPrivileged
    priv = MacPrivileged(runner)
    runner.on(["sudo", "-n", "true"], Result(1))
    priv.setup_dns("test", 5354)
    script = runner.calls[-1]
    assert script[0] == "osascript" and "with administrator privileges" in script[2]
    assert "/etc/resolver/test" in script[2] and "port 5354" in script[2]
    assert not priv.setup_dns("bad domain").ok
    ca = tmp_path / "rootCA.pem"
    ca.write_text("x")
    runner.responses.clear()
    priv.trust_ca(ca)
    assert runner.calls[-1][:3] == ["sudo", "-n", "/bin/sh"] and "add-trusted-cert" in runner.calls[-1][-1]
    assert not priv.trust_ca(tmp_path / "evil.pem").ok


def test_mac_paths_and_dns(macos, tmp_path):
    from pipanel.core.paths import Paths
    p = Paths.from_env({"HOME": str(tmp_path)})
    assert p.config == tmp_path / "Library" / "PIPanel" / "config"
    assert " " not in str(p.run.relative_to(tmp_path))


def test_windows_paths(windows, tmp_path):
    from pipanel.core.paths import Paths
    p = Paths.from_env({"LOCALAPPDATA": str(tmp_path)})
    assert p.data == tmp_path / "PIPanel" / "data"


def test_platform_names(windows):
    assert platform.exe("nginx") == "nginx.exe" and platform.exe("php.exe") == "php.exe"


def test_redis_paths_on_windows(windows, paths):
    from pipanel.core.redis import Redis, cygwin_path
    assert cygwin_path("C:\\Users\\Me\\AppData\\Local\\PIPanel\\config\\redis\\redis.conf") == \
        "/cygdrive/c/Users/Me/AppData/Local/PIPanel/config/redis/redis.conf"
    r = Redis(paths, server="C:/stack/redis/redis-server.exe")
    assert r.unit_spec()["argv"][1].endswith("/config/redis/redis.conf")


def test_transcript_lines():
    from pipanel.core.privileged import transcript_lines
    text = """**********************
Windows PowerShell transcript start
Start time: 20261018183000
Username: RUNNER\\runneradmin
**********************
PIPanel: .test now resolves through 127.0.0.1
ERROR: only C:\\Users\\x\\mkcert\\rootCA.pem may be trusted
**********************
Windows PowerShell transcript end
End time: 20261018183001
**********************"""
    assert transcript_lines(text) == ("PIPanel: .test now resolves through 127.0.0.1\n"
                                      "ERROR: only C:\\Users\\x\\mkcert\\rootCA.pem may be trusted")
