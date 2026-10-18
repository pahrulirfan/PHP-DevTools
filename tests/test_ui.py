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
import tempfile as _tempfile
from pathlib import Path as _Path
THEME_CACHE = _Path(_tempfile.mkdtemp(prefix="pipanel-theme-"))   # same files for every window: same stylesheet
@pytest.fixture
def win(qapp, ui_ctl, tmp_path):
    tm = theme.ThemeManager(qapp, "light", cache_dir=THEME_CACHE)
    w = MainWindow(ui_ctl, tm)
    w.show()
    yield w
    w.quitting = True
    w.close()
    w.monitor._timer.stop()   # old windows stay alive (deleting them crashes PySide at exit); keep them quiet
    w.tasks.wait(2000)


def test_every_page_renders(qapp, win):
    titles = []
    for i, page in enumerate(win.pages):
        win.show_page(i)
        pump(qapp, win, 6)
        titles.append(page.title)
        assert win.title.text() == page.title
    assert titles == ["Sites", "Laravel", "PHP", "Node", "Database", "Mail", "Share", "Services", "Logs", "Settings"]


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


# ---------- tray, sidebar status, new pages ----------
def active_runner(runner, active: set[str]):
    from pipanel.core.services import unit_name

    def run(argv, **kw):
        runner.calls.append([str(a) for a in argv])
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            names = [str(a) for a in argv[3:]]
            return Result(0, "\n".join("active" if n in {unit_name(s) for s in active} else "inactive"
                                      for n in names) + "\n")
        return Result(0)
    runner.run = run


def test_tray_menu_lists_running_services_and_php_users(qapp, win, ui_ctl, runner):
    from pipanel.ui.tray import Tray
    active_runner(runner, {"nginx", "dns", "php8.3"})
    tray = Tray(ui_ctl, win, qapp, win.monitor)
    tray.about_to_show()
    texts = [a.text() for a in tray.menu.actions()]
    assert "Services: 3 of 3 running" in texts
    php = next(a for a in tray.menu.actions() if a.text().startswith("PHP-FPM 8.3"))
    assert "blog, plain" in php.text() and "(running)" in php.text()
    from pipanel.ui.tray import names_hint
    assert names_hint([f"site{i}" for i in range(250)]) == "250 sites"
    assert names_hint(["a-very-long-project-name-indeed"]) == "a-very-long-proje…"
    assert [a.text() for a in php.menu().actions()] == ["Stop", "Restart"]
    assert not any(t.startswith("PHP-FPM 8.2") for t in texts)   # unused and stopped: hidden
    default = next(a for a in tray.menu.actions() if a.text().startswith("Default PHP"))
    checked = [a.text() for a in default.menu().actions() if a.isChecked()]
    assert checked and checked[0].startswith("PHP 8.3")
    assert "Quit" in texts and tray.icon.toolTip() == "PIPanel: 3 of 3 services running"


def test_tray_start_action_runs_in_background(qapp, win, ui_ctl, runner):
    from pipanel.core.services import unit_name
    from pipanel.ui.tray import Tray
    active_runner(runner, {"dns"})
    tray = Tray(ui_ctl, win, qapp, win.monitor)
    tray.about_to_show()
    nginx = next(a for a in tray.menu.actions() if a.text().startswith("Nginx"))
    assert "(not created)" in nginx.text() and nginx.menu().actions()[0].text() == "Start"
    nginx.menu().actions()[0].trigger()
    pump(qapp, win, 10)
    assert ["systemctl", "--user", "start", unit_name("nginx")] in runner.calls


def test_sidebar_status_shows_running_services(qapp, win, ui_ctl, runner):
    active_runner(runner, {"nginx", "php8.3"})
    win.monitor.refresh()
    pump(qapp, win, 15)
    side = win.side_status
    texts = [w.text() for w in side.findChildren(type(side.head))]
    assert "Services  ·  2/3 running" in texts and "Nginx" in texts and "PHP 8.3" in texts
    assert "PHP 8.2" not in texts
    win.go("Settings")
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    ev = QMouseEvent(QMouseEvent.MouseButtonRelease, QPointF(5, 5), QPointF(5, 5), Qt.LeftButton, Qt.LeftButton,
                     Qt.NoModifier)
    side.mouseReleaseEvent(ev)
    assert win.title.text() == "Services"


def test_sites_page_lists_proxies(qapp, win, ui_ctl):
    ui_ctl.add_proxy("next", "3000")
    win.go("Sites")
    page = page_of(win, "Sites")
    page.refresh()
    pump(qapp, win, 4)
    from pipanel.ui.pages.sites import ProxyRow
    rows = page.findChildren(ProxyRow)
    assert [r.proxy.name for r in rows] == ["next"]


def test_mail_page_installs_mailpit(qapp, win, ui_ctl):
    import io as _io
    import tarfile as _tar
    b = _io.BytesIO()
    with _tar.open(fileobj=b, mode="w:gz") as t:
        info = _tar.TarInfo("mailpit"); info.size = 3
        t.addfile(info, _io.BytesIO(b"BIN"))
    ui_ctl.fetch = lambda u: b.getvalue()
    win.go("Mail")
    page = page_of(win, "Mail")
    pump(qapp, win, 4)
    assert not page.install_btn.isHidden()
    page.install()
    pump(qapp, win, 20)
    assert ui_ctl.mailpit.installed() and page.install_btn.isHidden() and not page.inbox_btn.isHidden()


def test_laravel_page_starts_a_process(qapp, win, ui_ctl, runner, monkeypatch):
    from pipanel.core.services import unit_name
    monkeypatch.setattr(ui_ctl.services, "failures", lambda names, **kw: {})
    win.go("Laravel")
    page = page_of(win, "Laravel")
    page.refresh()
    pump(qapp, win, 4)
    assert set(page.proc_widgets) == {"queue", "schedule"}
    dot, tg = page.proc_widgets["queue"]
    tg.click()
    pump(qapp, win, 15)
    assert ["systemctl", "--user", "restart", unit_name("site-blog-queue")] in runner.calls


def test_logs_page_filters_laravel_levels(qapp, win, ui_ctl):
    logdir = ui_ctl.site("blog").path / "storage" / "logs"
    logdir.mkdir(parents=True)
    (logdir / "laravel.log").write_text("[2026-10-18 09:00:00] local.DEBUG: noise\n"
                                        "[2026-10-18 09:00:01] local.ERROR: boom\n#0 trace\n")
    win.go("Logs")
    page = page_of(win, "Logs")
    pump(qapp, win, 6)
    page.source.setCurrentText("blog (laravel.log)")
    page.level.setCurrentIndex(page.level.findData("error"))
    page.load()
    pump(qapp, win, 10)
    assert "boom" in page.view.toPlainText() and "#0 trace" in page.view.toPlainText()
    assert "noise" not in page.view.toPlainText() and not page.level.isHidden()
    page.source.setCurrentText("Nginx (error)")
    page.load()
    assert page.level.isHidden() and page.clear_btn.isHidden()


def test_share_page_cloudflare_needs_no_token(qapp, win, ui_ctl):
    win.go("Share")
    page = page_of(win, "Share")
    pump(qapp, win, 6)
    page.radio["cloudflare"].setChecked(True)
    pump(qapp, win, 10)
    assert ui_ctl.cfg.share_provider == "cloudflare"
    assert page.auth.isHidden() and page.start.isEnabled()
    page.radio["ngrok"].setChecked(True)
    pump(qapp, win, 10)
    assert not page.auth.isHidden() and not page.start.isEnabled()   # no verified token


def test_php_page_shows_xdebug(qapp, win, ui_ctl):
    win.go("PHP")
    page = page_of(win, "PHP")
    pump(qapp, win, 6)
    toggles = [w for w in page.xdebug.findChildren(Toggle)]
    assert len(toggles) == 1   # 8.3 has xdebug in mods-available; 8.2 offers "Install Xdebug"
    toggles[0].click()
    pump(qapp, win, 10)
    assert ui_ctl.php.xdebug_state("8.3") == "on"


def test_sites_page_shows_a_new_folder_by_itself(qapp, win, ui_ctl, tmp_path):
    win.go("Sites")
    page = page_of(win, "Sites")
    pump(qapp, win, 4)
    from pipanel.ui.pages.sites import SiteRow
    (tmp_path / "PHPProject" / "fresh").mkdir()
    page._fs_timer.setInterval(10)
    import time as _t
    for _ in range(100):
        pump(qapp, win, 2)
        if any(r.site.name == "fresh" for r in page.findChildren(SiteRow)):
            break
        _t.sleep(0.02)
    assert "fresh" in [r.site.name for r in page.findChildren(SiteRow) if r.isVisibleTo(page)]


def test_database_page_table_and_tools(qapp, win, ui_ctl, runner, monkeypatch):
    from pipanel.ui.pages.mysql import human_size
    assert human_size(512) == "512 B" and human_size(49152) == "48.0 KB" and human_size(3 * 1024 ** 3) == "3.0 GB"
    page = page_of(win, "Database")
    page.show_databases([("blog", 12, 49152), ("shop-api", 3, 2048), ("news", 0, 0)])
    tbl = page.table
    assert tbl.keys_shown() == ["blog", "news", "shop-api"] and not tbl.table.isHidden()
    tbl._sort_clicked(2)   # by size: sorted by bytes, not by the text
    assert tbl.keys_shown() == ["news", "shop-api", "blog"]
    tbl._sort_clicked(2)
    assert tbl.keys_shown() == ["blog", "shop-api", "news"]
    page.filter.setText("sho")
    assert tbl.keys_shown() == ["shop-api"]
    page.filter.clear()
    page.select_db("blog")
    assert page.selected_db() == "blog"
    removed = []
    monkeypatch.setattr(win, "confirm", lambda _t: True)
    monkeypatch.setattr(ui_ctl.mysql, "drop_database", removed.append)
    row = tbl.keys_shown().index("shop-api")
    from PySide6.QtWidgets import QPushButton
    menu = tbl.table.cellWidget(row, 3).findChildren(QPushButton)[0].menu()
    menu.aboutToShow.emit()
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts[0] == "Install phpMyAdmin or Adminer to browse it…" and "Back up (.sql.gz)…" in texts
    next(a for a in menu.actions() if a.text() == "Delete…").trigger()
    pump(qapp, win, 10)
    assert removed == ["shop-api"]
    page.show_databases([])
    assert tbl.table.isHidden() and not tbl.empty.isHidden()
    page.show_tools()
    texts = [w.text() for w in page.tools.findChildren(type(tbl.empty))]
    assert "phpMyAdmin" in texts and "Adminer" in texts


def test_database_page_pages_hundreds_of_databases(qapp, win, ui_ctl):
    page = page_of(win, "Database")
    tbl = page.table
    page.show_databases([(f"db{i:03d}", i, i * 1000) for i in range(245)])
    assert len(tbl.keys_shown()) == 20 and tbl.table.rowCount() == 20     # only one page of rows exists
    assert not tbl.pager.isHidden() and tbl.pager.info.text() == "1–20 of 245"
    tbl.pager.next.click()
    assert tbl.keys_shown()[0] == "db020" and tbl.pager.info.text() == "21–40 of 245"
    tbl.pager.go(99)
    assert tbl.keys_shown()[-1] == "db244" and not tbl.pager.next.isEnabled()
    page.filter.setText("db13")   # the filter searches every page and starts again at page 1
    assert tbl.keys_shown() == [f"db13{i}" for i in range(10)] and tbl.pager.isHidden()
    page.filter.clear()
    page.select_db("db150")         # selecting turns to the right page
    assert page.selected_db() == "db150" and tbl.pager.page == 7


def test_users_table(qapp, win, ui_ctl, monkeypatch):
    page = page_of(win, "Database")
    page.show_users([("root", "localhost", "*"), ("blog", "localhost", "blog, blog_test"), ("tmp", "%", "")])
    rows = {k: page.user_table.table.item(i, 2).text() for i, k in enumerate(page.user_table.keys_shown())}
    assert rows == {"blog@localhost": "blog, blog_test", "root@localhost": "all databases", "tmp@%": "none"}
    dropped = []
    monkeypatch.setattr(win, "confirm", lambda _t: True)
    monkeypatch.setattr(ui_ctl.mysql, "drop_user", lambda u, h: dropped.append((u, h)))
    from PySide6.QtWidgets import QPushButton
    i = page.user_table.keys_shown().index("root@localhost")
    assert not page.user_table.table.cellWidget(i, 3).findChildren(QPushButton)   # root: in its own card
    i = page.user_table.keys_shown().index("tmp@%")
    menu = page.user_table.table.cellWidget(i, 3).findChildren(QPushButton)[0].menu()
    menu.aboutToShow.emit()
    next(a for a in menu.actions() if a.text() == "Delete…").trigger()
    pump(qapp, win, 10)
    assert dropped == [("tmp", "%")]


def test_sites_page_marks_a_cloned_project(qapp, win, ui_ctl):
    win.go("Sites")
    page = page_of(win, "Sites")
    page.refresh()
    pump(qapp, win, 4)
    from pipanel.ui.pages.sites import SiteRow
    rows = {r.site.name: r for r in page.findChildren(SiteRow) if r.isVisibleTo(page)}
    def badges(row):
        from PySide6.QtWidgets import QPushButton
        return ["Needs setup" for w in row.findChildren(QPushButton) if w.property("needsSetup")]
    assert "Needs setup" in badges(rows["blog"])         # Laravel without .env
    assert "Needs setup" not in badges(rows["plain"])
    (ui_ctl.site("blog").path / ".env").write_text("APP_KEY=base64:x\n")
    page.refresh()
    pump(qapp, win, 4)
    rows = {r.site.name: r for r in page.findChildren(SiteRow) if r.isVisibleTo(page)}
    assert "Needs setup" not in badges(rows["blog"])


# ---------- notifications, Ctrl+K, recent sites ----------
def test_broke_tells_a_crash_from_a_stop():
    from pipanel.ui.notify import broke
    assert broke("active", "failed") and broke(None, "failed") and broke("active", "activating")
    assert not broke("active", "inactive") and not broke("inactive", "activating") and not broke(None, "active")


def test_watchdog_reports_a_crash_once_with_the_reason(qapp, win, ui_ctl, runner, monkeypatch):
    shown = []
    monkeypatch.setattr(win.notifier, "show", lambda name, title, body: shown.append((name, title, body)))
    runner.on(["journalctl"], Result(0, "Starting x\nbind() to 0.0.0.0:80 failed (98: Address already in use)\n"))
    runner.on(["ss"], Result(0, 'LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("apache2",pid=9,fd=3))\n'))
    wd = win.watchdog
    wd.check({"nginx": "active", "dns": "active"})
    wd.check({"nginx": "failed", "dns": "active"})
    pump(qapp, win, 10)
    wd.check({"nginx": "failed", "dns": "inactive"})   # still broken: not again; dns was stopped: not a crash
    pump(qapp, win, 10)
    assert len(shown) == 1 and shown[0][1] == "Nginx stopped working"
    assert "Address already in use" in shown[0][2] and "apache2" in shown[0][2]
    assert not win.problem.isHidden() and "Nginx stopped working" in win.problem.text.text()
    win._maybe_fixed({"nginx": "active"})
    assert win.problem.isHidden()
    wd.check({"nginx": "active"})
    wd.check({"nginx": "failed"})                        # a new breakdown is reported again
    pump(qapp, win, 10)
    assert len(shown) == 2
    ui_ctl.cfg.notify = False
    wd.check({"nginx": "active"}); wd.check({"nginx": "failed"})
    pump(qapp, win, 10)
    assert len(shown) == 2 and not win.problem.isHidden()   # the banner still shows it


def test_notification_action_restarts_the_service(qapp, win, ui_ctl, runner):
    from pipanel.core.services import unit_name
    from pipanel.ui.notify import Notifier
    import subprocess as sp
    calls = []

    def spawn(argv, **kw):
        calls.append(argv)
        return sp.CompletedProcess(argv, 0, "restart\n", "")
    n = Notifier(spawn=spawn)
    n.has_notify_send = True
    got = []
    n.action.connect(lambda s, k: got.append((s, k)))
    n.show("nginx", "Nginx stopped working", "why")
    for _ in range(50):
        pump(qapp, win, 1)
        if got:
            break
    assert got == [("nginx", "restart")]
    assert "--action=restart=Restart" in calls[0] and calls[0][-2:] == ["Nginx stopped working", "why"]
    win.watchdog.act("nginx", "restart")
    pump(qapp, win, 10)
    assert ["systemctl", "--user", "restart", unit_name("nginx")] in runner.calls


def test_palette_finds_sites_and_actions(qapp, win, ui_ctl, monkeypatch):
    from pipanel.ui.palette import Palette, matches
    opened = []
    monkeypatch.setattr("pipanel.core.actions.open_url", opened.append)
    p = Palette(win)
    texts = lambda: [p.list.item(i).text() for i in range(p.list.count())]
    p.query.setText("blog code")
    assert texts() == ["blog.test   ·   Open in VS Code"]
    p.query.setText("pla")
    assert texts()[0] == "plain.test   ·   Open in browser"
    p.run_current()
    assert opened == ["http://plain.test"] and ui_ctl.cfg.recent_sites[0] == "plain"
    p = Palette(win)
    p.query.setText("go to data")
    p.run_current()
    assert win.title.text() == "Database"
    many = [type(p.entries[0])(f"site{i}.test", "Open in browser", lambda: None) for i in range(500)]
    assert len(matches(many, "site")) == 60 and matches(many, "site42")[0].title == "site42.test"


def test_tray_lists_recent_sites_only(qapp, win, ui_ctl, runner):
    from pipanel.ui.tray import Tray
    ui_ctl.opened("plain")
    tray = Tray(ui_ctl, win, qapp, win.monitor)
    tray.about_to_show()
    menu = next(a.menu() for a in tray.menu.actions() if a.text() == "Open site")
    assert [a.text() for a in menu.actions()] == ["plain.test", "blog.test"]   # the recent one first
    assert [a.text() for a in menu.actions()[0].menu().actions()] == [
        "Open in browser", "Open in VS Code", "Open terminal", "Open folder"]


def test_recent_sites_order_and_limit(ui_ctl, tmp_path):
    for i in range(12):
        (tmp_path / "PHPProject" / f"p{i:02d}").mkdir()
    for n in ("p05", "blog", "p05", "p11"):
        ui_ctl.opened(n)
    assert ui_ctl.cfg.recent_sites == ["p11", "p05", "blog"]
    names = [s.name for s in ui_ctl.recent_sites(8)]
    assert names[:3] == ["p11", "p05", "blog"] and len(names) == 8
    for i in range(10):
        ui_ctl.opened(f"p{i:02d}")
    assert len(ui_ctl.cfg.recent_sites) == 8


def test_sites_filter_for_many_sites(qapp, win, ui_ctl, tmp_path):
    for i in range(8):
        (tmp_path / "PHPProject" / f"client-{i}").mkdir()
    page = page_of(win, "Sites")
    win.go("Sites")
    page.refresh()
    pump(qapp, win, 4)
    assert not page.filter.isHidden()
    page.filter.setText("client-3")
    pump(qapp, win, 4)
    from pipanel.ui.pages.sites import SiteRow
    shown = [r.site.name for r in page.findChildren(SiteRow) if r.isVisibleTo(page)]
    assert shown == ["client-3"]
    page.filter.clear()


def test_site_card_has_one_actions_menu_with_icons(qapp, win, ui_ctl, monkeypatch):
    win.go("Sites")
    page = page_of(win, "Sites")
    page.refresh()
    pump(qapp, win, 4)
    from pipanel.ui.pages.sites import SiteRow
    row = next(r for r in page.findChildren(SiteRow) if r.isVisibleTo(page) and r.site.name == "blog")
    menu = row.actions.menu()
    menu.aboutToShow.emit()
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts[:4] == ["Open in browser", "Open in VS Code", "Open terminal", "Open folder"]
    assert "Laravel: packages and processes" in texts and "Set up the project…" in texts
    assert all(not a.icon().isNull() for a in menu.actions() if not a.isSeparator())
    next(a for a in menu.actions() if a.text() == "View logs").trigger()
    assert win.title.text() == "Logs" and page_of(win, "Logs").source.currentText() == "blog (laravel.log)"
    win.go("Sites")
    next(a for a in menu.actions() if a.text() == "Share publicly…").trigger()
    assert win.title.text() == "Share" and page_of(win, "Share").site.currentText() == "blog"


def test_sites_page_pages_hundreds_of_sites(qapp, win, ui_ctl, tmp_path):
    for i in range(130):
        (tmp_path / "PHPProject" / f"client-{i:03d}").mkdir()
    page = page_of(win, "Sites")
    win.go("Sites")
    page.refresh()
    pump(qapp, win, 4)
    from pipanel.ui.pages.sites import SiteRow
    shown = lambda: [r.site.name for r in page.findChildren(SiteRow) if r.isVisibleTo(page)]
    assert len(shown()) == 20 and page.pager.info.text() == "1–20 of 132"
    assert page.count_label.text() == "132 sites"
    page.pager.go(6)
    pump(qapp, win, 4)
    assert shown() == [f"client-{i}" for i in range(119, 130)] + ["plain"]   # blog is first: 132 = 6 x 20 + 12
    page.filter.setText("client-05")
    pump(qapp, win, 4)
    assert shown() == [f"client-05{i}" for i in range(10)] and page.pager.isHidden()
