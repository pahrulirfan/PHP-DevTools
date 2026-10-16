import os, sys, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = __import__("pathlib").Path(__file__).resolve().parent.parent   # repo root, wherever it is checked out
sys.path.insert(0, str(REPO))
from pathlib import Path
from PySide6.QtWidgets import QApplication
from pipanel.core.controller import Controller
from pipanel.core.paths import Paths
from pipanel.core.runner import Result, Runner
from pipanel.core.php import PHP

class Fake(Runner):
    def run(self, argv, **kw):
        a = [str(x) for x in argv]
        if a[:3] == ["systemctl", "--user", "is-active"]:
            return Result(3, "\n".join(["active", "active", "inactive", "failed"][i % 4] for i in range(len(a) - 3)) + "\n")
        return Result(0, "")

tmp = Path(tempfile.mkdtemp())
proj = tmp / "PHPProject"
for n, lar in (("blog", True), ("shop-api", True), ("old site", False), ("wp", False)):
    d = proj / n; d.mkdir(parents=True)
    if lar: (d/"artisan").write_text(""); (d/"public").mkdir(); (d/"public/index.php").write_text("")
    elif n == "wp": (d/"wp-config.php").write_text("")
    else: (d/"index.php").write_text("")
paths = Paths.under(tmp / "dp"); paths.ensure()
sbin = tmp / "sbin"; sbin.mkdir()
for v in ("8.2", "8.3"): (sbin / f"php-fpm{v}").write_text("")
etc = tmp/"etc"/"8.3"/"mods-available"; etc.mkdir(parents=True)
for e in ("mbstring","xml","curl","zip","mysql","gd","intl","bcmath","xdebug","redis","opcache","sqlite3"): (etc/f"{e}.ini").write_text("; priority=20\n")
php = PHP(paths, etc_php=tmp/"etc", sbin=sbin, usr_bin=tmp)
helper = tmp/"helper"; helper.write_text("")
from pipanel.core.privileged import Privileged
ctl = Controller(paths, Fake(), home=tmp/"home", php=php, privileged=Privileged(Fake(), helper))
ctl.services._nginx_version = "1.24.0"
ctl.park(proj)
(proj/"blog"/"composer.json").write_text('{"require": {"laravel/framework": "^11", "livewire/livewire": "^3"}, "require-dev": {"laravel/pint": "^1"}}')
ctl.cfg.sites["blog"] = type(ctl.cfg.site("blog"))(php="8.2", https=True)
ctl.link("scratch", tmp)
(proj/"wp"/"web").mkdir(); ctl.cfg.sites["wp"] = type(ctl.cfg.site("wp"))(docroot="web")
ctl.php.ensure_conf_d("8.3")
for e in ("mbstring","xml","curl","opcache"): ctl.php.set_extension("8.3", e, True)
ctl.php.write_cli_wrapper("8.3")
logd = paths.logs; logd.mkdir(parents=True, exist_ok=True)
(logd/"blog-access.log").write_text("127.0.0.1 - - [16/Oct/2026:19:30:01] \"GET / HTTP/1.1\" 200 612\n"*40)
app = QApplication([])
from pipanel.app import create
app, win, tray = create(ctl, app)
out = REPO / "docs" / "screens"; out.mkdir(parents=True, exist_ok=True)
win.resize(1040, 680)
win.show()
import time
for mode in ("light", "dark"):
    win.themer.apply(mode)
    for i, p in enumerate(win.pages):
        win.show_page(i)
        for _ in range(30):
            win.tasks.wait(50); app.processEvents(); time.sleep(0.01)
        if p.title == "Services":
            p.run_doctor()
            for _ in range(30): win.tasks.wait(50); app.processEvents()
        for _ in range(3): app.processEvents(); time.sleep(0.05)
        win.grab().save(str(out / f"{mode}-{p.title.lower()}.png"))
        if p.title == "PHP":
            p.ext_version.setCurrentIndex(p.ext_version.findData("8.3")); p.refresh_ext()
            for _ in range(5): app.processEvents(); time.sleep(0.03)
            p.ext.adjustSize()
            p.ext.grab().save(str(out / f"{mode}-php-extensions.png"))
print(sorted(x.name for x in out.iterdir()))
