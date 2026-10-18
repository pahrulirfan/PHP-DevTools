import pytest

from pipanel.core.paths import Paths
from pipanel.core.runner import Result, Runner


class FakeRunner(Runner):
    """Records commands; `responses` maps an argv prefix -> Result."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []   # stdin given to each call
        self.responses: list[tuple[list[str], Result]] = []

    def on(self, prefix, result):
        self.responses.append((list(prefix), result))

    def run(self, argv, **kw):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        self.last_kw = kw
        self.inputs.append(kw.get("input"))
        for prefix, res in self.responses:
            if argv[:len(prefix)] == prefix:
                return res
        return Result(0, "", "")

    def ran(self, *prefix):
        return any(c[:len(prefix)] == list(prefix) for c in self.calls)


def pytest_configure(config):
    config.addinivalue_line("markers", "native: runs with the real platform detection of the machine")
    config.addinivalue_line("markers", "posix: needs a POSIX shell and file system (skipped on Windows)")


@pytest.fixture(autouse=True)
def simulate_linux(request, monkeypatch):
    """Most tests check the Linux code paths, on any machine. Tests for Windows and macOS set the platform
    themselves; `native` tests use the machine's own."""
    if "native" not in request.keywords:
        monkeypatch.setenv("PIPANEL_PLATFORM", "linux")
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "PIPANEL_HOME", "NGROK_AUTHTOKEN"):
        monkeypatch.delenv(var, raising=False)   # CI machines set some of these (and may have an ngrok config)


def pytest_collection_modifyitems(config, items):
    import sys
    if sys.platform.startswith("win"):
        skip = pytest.mark.skip(reason="needs a POSIX system")
        for item in items:
            if "posix" in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def paths(tmp_path):
    p = Paths.under(tmp_path)
    p.ensure()
    return p


@pytest.fixture
def runner():
    return FakeRunner()


# ---------- fixture UI ----------
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def no_system_db(monkeypatch):
    """Tests must not depend on a MySQL/MariaDB that happens to be installed on the machine running them."""
    import dataclasses
    import pipanel.core.mysql as m
    monkeypatch.setattr(m, "ENGINES", {k: dataclasses.replace(e, server=f"/nonexistent/{k}d")
                                       for k, e in m.ENGINES.items()})


@pytest.fixture
def ui_ctl(paths, runner, tmp_path, no_system_db):
    from pipanel.core.controller import Controller
    from pipanel.core.php import PHP
    from pipanel.core.privileged import Privileged

    proj = tmp_path / "PHPProject"
    for name, laravel in (("blog", True), ("plain", False)):
        d = proj / name
        d.mkdir(parents=True)
        if laravel:
            (d / "artisan").write_text(""); (d / "public").mkdir(); (d / "public/index.php").write_text("")
        else:
            (d / "index.php").write_text("")
    sbin = tmp_path / "sbin"; sbin.mkdir()
    (sbin / "php-fpm8.3").write_text(""); (sbin / "php-fpm8.2").write_text("")
    mods = tmp_path / "etc" / "8.3" / "mods-available"; mods.mkdir(parents=True)
    for e in ("mbstring", "xdebug"):
        (mods / f"{e}.ini").write_text("; priority=20\n")
    helper = tmp_path / "helper"; helper.write_text("")
    ctl = Controller(paths, runner, home=tmp_path / "home", fetch=lambda u: b"",
                     php=PHP(paths, etc_php=tmp_path / "etc", sbin=sbin, usr_bin=tmp_path),
                     privileged=Privileged(runner, helper))
    ctl.services._nginx_version = "1.24.0"
    ctl.services.nginx_bin = str(tmp_path / "no-nginx")
    ctl.park(proj)
    ctl.php.ensure_conf_d("8.3")
    return ctl


def pump(app, win=None, rounds: int = 20):
    import time
    for _ in range(rounds):
        if win is not None:
            win.tasks.wait(30)
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture(autouse=True)
def fail_on_unhandled_qt_exceptions():
    """Qt swallows exceptions raised inside virtual overrides (showEvent, paintEvent, slots) and only prints them,
    so a broken page would still pass. Turn them into test failures."""
    import sys
    seen = []
    old = sys.excepthook
    sys.excepthook = lambda tp, val, tb: (seen.append(val), old(tp, val, tb))
    yield
    sys.excepthook = old
    assert not seen, f"unhandled exception(s) inside Qt callbacks: {seen}"
