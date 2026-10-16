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


def test_cli_wrapper(paths, fake_etc):
    php = PHP(paths, **fake_etc)
    w = php.write_cli_wrapper("8.3")
    text = w.read_text()
    assert w.stat().st_mode & 0o111
    assert f'PHP_INI_SCAN_DIR="{php.conf_d("8.3")}"' in text and "php8.3" in text
    assert php.cli_version() == "8.3"


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
