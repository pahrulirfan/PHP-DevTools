import pytest

from pipanel.core import templates as t


def test_site_http_only(paths):
    out = t.render_site(paths, name="blog", docroot="/p/blog/public", php_version="8.3")
    assert "server_name blog.test;" in out
    assert "root /p/blog/public;" in out
    assert f"fastcgi_pass unix:{paths.run}/php8.3.sock;" in out
    assert "443" not in out and "ssl_certificate" not in out


def test_site_https_old_nginx_uses_http2_on_listen(paths):
    out = t.render_site(paths, name="blog", docroot="/p", php_version="8.3", https=True,
                        nginx_version="1.18.0")
    assert "listen 443 ssl http2;" in out and "http2 on;" not in out
    assert f"ssl_certificate     {paths.certs}/blog.test.pem;" in out


def test_site_https_new_nginx_uses_http2_directive(paths):
    out = t.render_site(paths, name="blog", docroot="/p", php_version="8.3", https=True,
                        nginx_version="1.26.1")
    assert "listen 443 ssl;" in out and "http2 on;" in out


@pytest.mark.parametrize("bad", ["", "Blog", "a b", "a;rm", "../x", "-a", "a-", "a.b"])
def test_site_rejects_bad_names(paths, bad):
    with pytest.raises(ValueError):
        t.render_site(paths, name=bad, docroot="/p", php_version="8.3")


@pytest.mark.parametrize("bad", ["8", "7.4", "8.10", "8.3; x", ""])
def test_rejects_bad_php_versions(paths, bad):
    with pytest.raises(ValueError):
        t.render_php_fpm(paths, bad)


def test_nginx_conf_is_user_owned(paths):
    out = t.render_nginx_conf(paths)
    assert "daemon off;" in out
    assert f"include {paths.nginx_sites}/*.conf;" in out
    assert "/var/" not in out and "\nuser " not in out
    assert f"pid {paths.run}/nginx.pid;" in out


def test_php_fpm_no_user_directive(paths):
    out = t.render_php_fpm(paths, "8.3")
    assert f"listen = {paths.run}/php8.3.sock" in out
    assert "daemonize = no" in out
    assert "\nuser" not in out and "\ngroup" not in out


def test_dnsmasq_and_resolved():
    assert "address=/test/127.0.0.1" in t.render_dnsmasq(paths=type("P", (), {"run": "/r"})(), port=5354)
    r = t.render_resolved_dropin(port=5354)
    assert "DNS=127.0.0.1:5354" in r and "Domains=~test" in r


def test_unit():
    out = t.render_unit(description="x", exec_start="/bin/true", exec_reload="/bin/kill -HUP $MAINPID",
                        env={"PHP_INI_SCAN_DIR": "/a:/b"})
    assert "ExecStart=/bin/true" in out and "ExecReload=/bin/kill -HUP $MAINPID" in out
    assert "Environment=PHP_INI_SCAN_DIR=/a:/b" in out
    assert "ExecReload" not in t.render_unit(description="x", exec_start="/bin/true")


@pytest.mark.parametrize("v,ok", [("1.18.0", False), ("1.24.0", False), ("1.25.1", True), ("1.26.3", True), ("junk", False)])
def test_http2_detection(v, ok):
    assert t.nginx_supports_http2_directive(v) is ok


def test_ports_are_configurable(paths):
    assert "listen 8080 default_server;" in t.render_nginx_conf(paths, http_port=8080)
    out = t.render_site(paths, name="a", docroot="/p", php_version="8.3", https=True,
                        nginx_version="1.24.0", http_port=8080, https_port=8443)
    assert "listen 8080;" in out and "listen 8443 ssl http2;" in out


def test_mysql_cnf_differs_per_engine(paths):
    kw = dict(datadir="/d", socket="/s", pid="/p", log="/l", port=3306)
    my, ma = t.render_mysql_cnf(**kw, engine="mysql"), t.render_mysql_cnf(**kw, engine="mariadb")
    assert "mysqlx=OFF" in my and "mysqlx" not in ma
    assert "datadir=/d" in ma and "port=3306" in ma
