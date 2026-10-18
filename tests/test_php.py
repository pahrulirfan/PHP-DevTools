import hashlib

import pytest

from pipanel.core.php import PHP, PHPError, validate_setting


@pytest.fixture
def fake_etc(tmp_path):
    etc, sbin, ubin = tmp_path / "etc-php", tmp_path / "sbin", tmp_path / "ubin"
    mods = etc / "8.3" / "mods-available"
    fpm = etc / "8.3" / "fpm" / "conf.d"
    mods.mkdir(parents=True), fpm.mkdir(parents=True), sbin.mkdir(), ubin.mkdir()
    (mods / "opcache.ini").write_text("; priority=10\nzend_extension=opcache\n")
    (mods / "mbstring.ini").write_text("; priority=20\nextension=mbstring\n")
    (mods / "xdebug.ini").write_text("; priority=20\nzend_extension=xdebug\n")
    (fpm / "10-opcache.ini").symlink_to(mods / "opcache.ini")
    (fpm / "20-mbstring.ini").symlink_to(mods / "mbstring.ini")
    (sbin / "php-fpm8.3").write_text("")
    (sbin / "php-fpm8.1").write_text("")
    (ubin / "php8.3").write_text("")
    return PHP_args(etc, sbin, ubin)


def PHP_args(etc, sbin, ubin):
    return dict(etc_php=etc, sbin=sbin, usr_bin=ubin)


def test_installed_sorted(paths, fake_etc):
    assert PHP(paths, **fake_etc).installed() == ["8.1", "8.3"]


def test_conf_d_seeded_once_and_settings(paths, fake_etc):
    php = PHP(paths, **fake_etc)
    php.ensure_conf_d("8.3", {"memory_limit": "1G"})
    d = php.conf_d("8.3")
    assert (d / "10-opcache.ini").is_symlink() and (d / "20-mbstring.ini").is_symlink()
    assert "memory_limit = 1G" in (d / "99-pipanel.ini").read_text()
    php.set_extension("8.3", "mbstring", False)
    php.ensure_conf_d("8.3")  # not seeded again
    assert not (d / "20-mbstring.ini").exists()


def test_extensions_toggle_uses_priority(paths, fake_etc):
    php = PHP(paths, **fake_etc)
    php.ensure_conf_d("8.3")
    assert dict(php.extensions("8.3")) == {"mbstring": True, "opcache": True, "xdebug": False}
    php.set_extension("8.3", "xdebug", True)
    assert (php.conf_d("8.3") / "20-xdebug.ini").is_symlink()
    assert dict(php.extensions("8.3"))["xdebug"] is True
    php.set_extension("8.3", "xdebug", False)
    assert dict(php.extensions("8.3"))["xdebug"] is False


def test_extension_errors(paths, fake_etc):
    php = PHP(paths, **fake_etc)
    php.ensure_conf_d("8.3")
    with pytest.raises(PHPError):
        php.set_extension("8.3", "../etc", True)
    with pytest.raises(PHPError):
        php.set_extension("8.3", "nope", True)


@pytest.mark.parametrize("k,v", [("memory_limit", "1G; x"), ("memory_limit", "abc"), ("bogus", "1"),
                                 ("max_execution_time", "-5"), ("post_max_size", "1\nfoo=1")])
def test_bad_settings(k, v):
    with pytest.raises(PHPError):
        validate_setting(k, v)


def test_good_settings():
    assert validate_setting("memory_limit", "-1") == "-1"
    assert validate_setting("upload_max_filesize", " 64M ") == "64M"


def fake_php_binary(ubin, v):
    """A stand-in for /usr/bin/php8.x that prints which version ran and its ini scan folder."""
    f = ubin / f"php{v}"
    f.write_text(f'#!/bin/sh\necho "{v} $PHP_INI_SCAN_DIR $*"\n')
    f.chmod(0o755)


def run_php(wrapper, cwd, **env):
    import os
    import subprocess
    out = subprocess.run([str(wrapper), "-v"], cwd=cwd, capture_output=True, text=True,
                         env={"PATH": os.environ["PATH"], **env})
    return out.stdout.split()


@pytest.mark.posix
def test_cli_wrapper(paths, fake_etc):
    php = PHP(paths, **fake_etc)
    fake_php_binary(fake_etc["usr_bin"], "8.3")
    w = php.write_cli_wrapper("8.3")
    assert w.stat().st_mode & 0o111
    assert php.cli_version() == "8.3"
    assert run_php(w, paths.data) == ["8.3", str(php.conf_d("8.3")), "-v"]


@pytest.mark.posix
def test_cli_wrapper_follows_the_site_version(paths, fake_etc, tmp_path):
    php = PHP(paths, **fake_etc)
    for v in ("8.2", "8.3", "8.4"):
        fake_php_binary(fake_etc["usr_bin"], v)
    blog, shop = tmp_path / "proj" / "blog", tmp_path / "proj" / "shop"
    (blog / "app" / "Models").mkdir(parents=True), shop.mkdir()
    nested = blog / "packages" / "inner"   # a site inside another site: the deepest folder wins
    nested.mkdir(parents=True)
    linked = tmp_path / "link-to-shop"
    linked.symlink_to(shop)
    php.write_site_map([(blog, "8.2"), (shop, "8.4"), (nested, "8.3"), (tmp_path / "gone", "9.9")])
    w = php.write_cli_wrapper("8.3")
    assert run_php(w, blog)[0] == "8.2"
    assert run_php(w, blog / "app" / "Models")[:2] == ["8.2", str(php.conf_d("8.2"))]
    assert run_php(w, nested)[0] == "8.3"
    assert run_php(w, shop)[0] == "8.4"
    assert run_php(w, linked)[0] == "8.4"            # reached through a symlink
    assert run_php(w, tmp_path)[0] == "8.3"           # outside every site: the default
    assert run_php(w, blog, PIPANEL_PHP="8.4")[0] == "8.4"
    assert run_php(w, blog, PIPANEL_PHP="nonsense")[0] == "8.3"
    (fake_etc["usr_bin"] / "php8.4").unlink()       # a site's version that is not installed: the default
    assert run_php(w, shop)[0] == "8.3"
    assert "9.9" not in php.site_map.read_text()
    assert not php.write_site_map([(blog, "8.2"), (shop, "8.4"), (nested, "8.3")])   # unchanged


def test_xdebug_toggle(paths, fake_etc):
    php = PHP(paths, **fake_etc)
    php.ensure_conf_d("8.3")
    assert php.xdebug_state("8.3") == "off"      # installed after the first seed: not enabled
    php.set_xdebug("8.3", True)
    assert php.xdebug_state("8.3") == "on"
    ini = (php.conf_d("8.3") / php.XDEBUG_INI).read_text()
    assert "xdebug.mode = debug" in ini and "client_port = 9003" in ini
    php.set_xdebug("8.3", False)
    assert php.xdebug_state("8.3") == "off" and not (php.conf_d("8.3") / php.XDEBUG_INI).exists()
    (fake_etc["etc_php"] / "8.3" / "mods-available" / "xdebug.ini").unlink()
    assert php.xdebug_state("8.3") == "missing"


def test_composer_checksum(paths, fake_etc):
    php = PHP(paths, **fake_etc)
    phar = b"PHAR"
    good = {"https://getcomposer.org/download/latest-stable/composer.phar": phar,
            "https://getcomposer.org/download/latest-stable/composer.phar.sha256sum":
                (hashlib.sha256(phar).hexdigest() + "  composer.phar\n").encode()}
    w = php.install_composer(lambda u: good[u])
    assert w.exists() and (paths.bin / "composer.phar").read_bytes() == phar
    bad = dict(good)
    bad[list(good)[1]] = b"0" * 64
    with pytest.raises(PHPError):
        php.install_composer(lambda u: bad[u])
