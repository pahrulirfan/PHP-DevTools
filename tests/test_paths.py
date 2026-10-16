from pathlib import Path

from pipanel.core.paths import Paths


def test_from_env_xdg():
    p = Paths.from_env({"HOME": "/h", "XDG_RUNTIME_DIR": "/run/user/9"})
    assert p.config == Path("/h/.config/pipanel")
    assert p.state == Path("/h/.local/state/pipanel")
    assert p.run == Path("/run/user/9/pipanel")
    assert p.systemd_user == Path("/h/.config/systemd/user")
    assert p.php_socket("8.3") == Path("/run/user/9/pipanel/php8.3.sock")


def test_xdg_overrides():
    p = Paths.from_env({"HOME": "/h", "XDG_CONFIG_HOME": "/c", "XDG_DATA_HOME": "/d"})
    assert p.config == Path("/c/pipanel") and p.data == Path("/d/pipanel")
    assert p.systemd_user == Path("/c/systemd/user")


def test_ensure_creates_dirs(paths):
    assert paths.nginx_sites.is_dir() and paths.certs.is_dir() and paths.logs.is_dir()
