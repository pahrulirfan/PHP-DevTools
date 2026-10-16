import json

import pytest
from PySide6.QtCore import QObject, QThread, Qt
from PySide6.QtGui import QPalette

from pipanel.core.runner import Result
from pipanel.ui import theme
from pipanel.ui.main_window import MainWindow
from pipanel.ui.tasks import Tasks
from pipanel.ui.widgets import Toggle

from .conftest import pump


def page_of(win, title):
    return next(p for p in win.pages if p.title == title)


# ---------- theme ----------
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_text_contrast_meets_aa(mode):
    t = theme.TOKENS[mode]
    for fg in ("text", "text-muted", "primary-text", "success-text", "warning-text", "danger-text", "info-text"):
        for bg in ("bg", "surface"):
            assert theme.contrast(t[fg], t[bg]) >= 4.5, (mode, fg, bg)
    for fg in ("text", "text-muted", "primary-text"):
        for bg in ("surface-2", "primary-soft"):
            assert theme.contrast(t[fg], t[bg]) >= 4.5, (mode, fg, bg)
    assert theme.contrast(t["on-primary"], t["primary"]) >= 4.5
    assert theme.contrast("#FFFFFF", t["danger-button"]) >= 4.5


def test_token_sets_match():
    assert set(theme.LIGHT) == set(theme.DARK)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_qss_fully_resolved(mode):
    out = theme.render_qss(theme.TOKENS[mode], {"arrow_down": "/x.png", "check_icon": "/c.png"})
    assert "{{" not in out and theme.TOKENS[mode]["primary"] in out


def test_qss_unknown_token_raises(monkeypatch, tmp_path):
    f = tmp_path / "t.qss"
    f.write_text("a { color: {{nope}}; }")
    monkeypatch.setattr(theme, "TEMPLATE", f)
    with pytest.raises(KeyError):
        theme.render_qss(theme.LIGHT)


def test_parse_gsettings():
    assert theme.parse_gsettings("'prefer-dark'\n", None) == "dark"
    assert theme.parse_gsettings("'default'", "'Mint-Y-Dark-Purple'") == "dark"
    assert theme.parse_gsettings(None, "'Mint-Y'") == "light"
    assert theme.parse_gsettings(None, None) is None
    assert theme.resolve("system", "dark") == "dark" and theme.resolve("light", "dark") == "light"


def test_theme_manager_applies_and_notifies(qapp, tmp_path):
    tm = theme.ThemeManager(qapp, "dark", cache_dir=tmp_path)
    seen = []
    tm.changed.connect(seen.append)
    assert tm.current == "dark" and qapp.palette().color(QPalette.Window).name().upper() == "#14111F"
    tm.apply("light")
    assert seen == ["light"] and qapp.palette().color(QPalette.Window).name().upper() == "#FAF9FC"
    assert theme.tokens()["bg"] == "#FAF9FC"
    tm.toggle()
    assert tm.current == "dark" and tm.mode == "dark"
    tm.apply("bogus")
    assert tm.mode == "system"
    tm.apply("light")


def test_system_mode_follows_detected_scheme(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(theme, "system_scheme", lambda: "dark")
    tm = theme.ThemeManager(qapp, "system", cache_dir=tmp_path)
    assert tm.current == "dark"
    monkeypatch.setattr(theme, "system_scheme", lambda: "light")
    tm._system_changed()
    assert tm.current == "light"
    tm.apply("light")


# ---------- widgets & tasks ----------
def test_toggle(qapp, tmp_path):
    theme.ThemeManager(qapp, "light", cache_dir=tmp_path)
    t = Toggle(False)
    t.click()
    assert t.isChecked()
    t.setChecked(False)
    assert not t.isChecked() and t._pos == 0.0
    t.setChecked(True)
    assert t._pos == 1.0


def test_tasks_callbacks_on_main_thread(qapp):
    tasks = Tasks()
    main = QThread.currentThread()
    seen = {}
    tasks.run(lambda: QThread.currentThread(), done=lambda t: seen.update(worker=t, cb=QThread.currentThread()))
    errors = []
    tasks.run(lambda: 1 / 0, failed=errors.append)
    for _ in range(50):
        tasks.wait(50); qapp.processEvents()
        if seen and errors:
            break
    assert seen["worker"] is not main and seen["cb"] is main
    assert isinstance(errors[0], ZeroDivisionError)
    assert tasks.infos == {}   # unlabeled tasks are not tracked


# ---------- main window ----------
@pytest.fixture
def win(qapp, ui_ctl, tmp_path):
    tm = theme.ThemeManager(qapp, "light", cache_dir=tmp_path / "theme")
    w = MainWindow(ui_ctl, tm)
    w.show()
    yield w
    w.quitting = True
    w.close()


def test_every_page_renders(qapp, win):
    titles = []
    for i, page in enumerate(win.pages):
        win.show_page(i)
        pump(qapp, win, 6)
        titles.append(page.title)
        assert win.title.text() == page.title
    assert titles == ["Sites", "Laravel", "PHP", "Node", "Database", "Share", "Services", "Logs", "Settings"]


def test_sites_page_lists_sites(qapp, win):
    win.go("Sites")
    pump(qapp, win, 4)
    from pipanel.ui.pages.sites import SiteRow
    rows = win.pages[0].findChildren(SiteRow)
    assert sorted(r.site.name for r in rows) == ["blog", "plain"]


def test_sites_page_empty_state(qapp, win):
    win.ctl.cfg.parked = []
    win.go("Sites")
    win.pages[0].refresh()
    pump(qapp, win, 4)
    from pipanel.ui.widgets import EmptyState
    assert win.pages[0].findChildren(EmptyState)


def test_https_toggle_creates_cert_and_applies(qapp, win, ui_ctl, runner):
    def fake(argv, **kw):
        if argv[0] == "mkcert":
            c, k = ui_ctl.certs.cert_paths("blog", "test"); c.write_text("c"); k.write_text("k")
        return Result(0)
    runner.run = fake
    page = win.pages[0]
    page.change_https(ui_ctl.site("blog"), True, Toggle(True))
    pump(qapp, win, 20)
    assert ui_ctl.site("blog").https
    assert "listen 443 ssl" in (ui_ctl.paths.nginx_sites / "blog.conf").read_text()


def test_https_failure_reverts_toggle_and_reports(qapp, win, ui_ctl, runner, monkeypatch):
    runner.on(["mkcert"], Result(1, "", "boom"))
    errors = []
    monkeypatch.setattr(win, "error", errors.append)
    tg = Toggle(True)
    win.pages[0].change_https(ui_ctl.site("blog"), True, tg)
    pump(qapp, win, 20)
    assert not tg.isChecked() and "boom" in str(errors[0])
    assert not ui_ctl.site("blog").https


def test_change_php_updates_config(qapp, win, ui_ctl):
    win.pages[0].change_php(ui_ctl.site("blog"), "8.2")
    pump(qapp, win, 20)
    assert ui_ctl.site("blog").php == "8.2"


def test_theme_button_and_settings_persist(qapp, win, ui_ctl):
    win.theme_btn.click()
    assert win.themer.current == "dark"
    win.set_theme("light")
    assert json.loads(ui_ctl.paths.config_file.read_text())["theme"] == "light"
    win.go("Settings")
    assert win.pages[-1].radios["light"].isChecked()


def test_services_page_shows_states(qapp, win, runner):
    runner.on(["systemctl", "--user", "is-active"], Result(3, "active\ninactive\nactive\n"))
    win.go("Services")
    pump(qapp, win, 15)
    rows = page_of(win, "Services").rows
    assert list(rows) == ["nginx", "dns", "php8.2", "php8.3"][:len(rows)] or set(rows) >= {"nginx", "dns"}
    assert rows["nginx"][2].isChecked() and not rows["dns"][2].isChecked()


def test_banner_depends_on_setup(qapp, win, ui_ctl):
    assert not win.banner.isVisibleTo(win)   # PHP and the helper are both present
    ui_ctl.priv.helper = ui_ctl.priv.helper.parent / "missing"
    win.update_banner()
    assert win.banner.isVisibleTo(win)


def test_close_hides_to_tray_only_when_available(qapp, win):
    win.tray_available = True
    win.close()
    assert not win.isVisible()
    win.show()
    win.tray_available = False
    win.close()
    assert not win.isVisible()


def test_logs_page_reads_file(qapp, win, ui_ctl):
    (ui_ctl.paths.logs / "blog-access.log").write_text("hello\nworld\n")
    win.go("Logs")
    page = page_of(win, "Logs")
    pump(qapp, win, 6)
    page.source.setCurrentText("blog (access)")
    page.load()
    pump(qapp, win, 10)
    assert "world" in page.view.toPlainText()


def test_php_page_toggle_extension(qapp, win, ui_ctl):
    win.go("PHP")
    pump(qapp, win, 8)
    page = page_of(win, "PHP")
    page.toggle_ext("8.3", "mbstring", True)
    pump(qapp, win, 10)
    assert dict(ui_ctl.php.extensions("8.3"))["mbstring"] is True


def test_change_web_root_dialog_flow(qapp, win, ui_ctl, monkeypatch):
    proj = next(iter(ui_ctl.sites()))
    (proj.path / "web").mkdir(exist_ok=True)
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("web", True)))
    win.pages[0].change_docroot(proj)
    pump(qapp, win, 20)
    assert ui_ctl.site(proj.name).custom_docroot


def test_settings_page_changes_projects_folder(qapp, win, ui_ctl, tmp_path, monkeypatch):
    new = tmp_path / "other"
    (new / "x").mkdir(parents=True)
    (new / "x" / "index.php").write_text("")
    old = ui_ctl.cfg.parked[0]
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(new)))
    win.go("Settings")
    page = win.pages[-1]
    page.change_folder(old)
    pump(qapp, win, 20)
    assert ui_ctl.cfg.parked == [str(new)] and [s.name for s in ui_ctl.sites()] == ["x"]


def test_export_icons_writes_png_for_each_size(qapp, tmp_path):
    from PySide6.QtGui import QImage
    from pipanel.ui.icons import export_icons
    files = export_icons(tmp_path, sizes=(16, 48))
    assert [f.parent.parent.name for f in files] == ["16x16", "48x48"]
    img = QImage(str(files[1]))
    assert (img.width(), img.height()) == (48, 48) and img.pixelColor(24, 24).alpha() == 255
    assert img.pixelColor(0, 0).alpha() == 0   # transparent corner: the squircle shape is kept


def test_database_page_switches_engine(qapp, win, ui_ctl, runner):
    from pipanel.ui.pages.mysql import MySQLPage
    win.go("Database")
    page = next(p for p in win.pages if isinstance(p, MySQLPage))
    pump(qapp, win, 8)
    assert page.engine.currentData() == "mysql" and page.install_btn.text() == "Install MySQL 8"
    page.switch_engine("mariadb")
    pump(qapp, win, 15)
    assert ui_ctl.cfg.db_engine == "mariadb"
    assert page.engine.currentData() == "mariadb" and page.install_btn.text() == "Install MariaDB"


def test_services_page_names_the_active_database(qapp, win, ui_ctl, runner):
    from pipanel.ui.pages.services import service_label
    assert service_label("mysql") == "MySQL" and service_label("mysql", "MariaDB") == "MariaDB"
    ui_ctl.set_db_engine("mariadb")
    assert ui_ctl.db_label == "MariaDB"


# ---------- Laravel packages page ----------
def test_laravel_page_lists_catalog_and_installed_state(qapp, win, ui_ctl):
    import json
    from pipanel.core.laravel import CATALOG
    blog = ui_ctl.site("blog")
    (blog.path / "composer.json").write_text(json.dumps({"require": {"livewire/livewire": "^3"}}))
    win.go("Laravel")
    page = page_of(win, "Laravel")
    pump(qapp, win, 6)
    assert page.site.currentText() == "blog"
    names = [l.text() for l in page.catalog.findChildren(type(page.where)) if l.objectName() == "cardTitle"]
    assert set(names) >= {p.name for p in CATALOG}
    installed = [l.text() for l in page.catalog.findChildren(type(page.where)) if l.text() == "Installed"]
    assert installed == ["Installed"]   # only Livewire


def test_laravel_page_installs_a_package_and_shows_output(qapp, win, ui_ctl, runner):
    from pipanel.core.laravel import BY_KEY
    (ui_ctl.paths.bin / "composer.phar").write_text("")
    win.go("Laravel")
    page = page_of(win, "Laravel")
    pump(qapp, win, 4)
    runner.on(["composer"], Result(0))
    page.install(BY_KEY["pint"])
    pump(qapp, win, 20)
    assert any(c[2:4] == ["require", "--dev"] and "laravel/pint" in c for c in runner.calls)
    assert page.log.toPlainText().startswith("✓ composer require --dev laravel/pint")


def test_laravel_page_rejects_bad_custom_package(qapp, win, ui_ctl, monkeypatch):
    errors = []
    monkeypatch.setattr(win, "error", errors.append)
    win.go("Laravel")
    page = page_of(win, "Laravel")
    page.pkg.setText("not a package; rm -rf /")
    page.install_custom()
    assert errors and "vendor/package" in errors[0]


def test_laravel_page_empty_state_without_laravel_sites(qapp, win, ui_ctl):
    ui_ctl.cfg.parked = []
    win.go("Laravel")
    page = page_of(win, "Laravel")
    page.refresh()
    assert not page.empty.isHidden() and page.catalog.isHidden()


def test_sites_page_warns_when_services_are_stopped(qapp, win, ui_ctl, runner):
    page = page_of(win, "Sites")
    runner.on(["systemctl", "--user", "is-active"], Result(3, "inactive\ninactive\n"))
    page.refresh()
    pump(qapp, win, 10)
    assert not page.stopped.isHidden()
    runner.responses.clear()
    runner.on(["systemctl", "--user", "is-active"], Result(0, "active\nactive\n"))
    page.refresh()
    pump(qapp, win, 10)
    assert page.stopped.isHidden()


def test_settings_page_applies_web_ports(qapp, win, ui_ctl):
    win.go("Settings")
    page = page_of(win, "Settings")
    page.http.setValue(8080)
    page.https.setValue(8443)
    page.apply_ports()
    pump(qapp, win, 15)
    assert (ui_ctl.cfg.http_port, ui_ctl.cfg.https_port) == (8080, 8443)
