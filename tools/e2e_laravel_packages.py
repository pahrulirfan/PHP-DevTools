"""Real test of the Laravel package installer: composer require/remove through the site's PHP (no root).

Usage: python tools/e2e_laravel_packages.py <dir with the extracted php8.3 debs in <dir>/root>
(a tiny stand-in project is used, so no framework download is needed)
"""
import json
import os
import shutil
import sys
from pathlib import Path

import os
os.environ.setdefault("PIPANEL_UNIT_PREFIX", "pipanel-e2e")   # never touch the units of a real installation
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
ROOT = Path(sys.argv[1]) / "root"
T = Path("/tmp/dpe2e4"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
from pipanel.core.laravel import LaravelTools, declared_packages
from pipanel.core.paths import Paths
from pipanel.core.php import PHP
from pipanel.core.runner import Runner

paths = Paths.under(T); paths.ensure()
php = PHP(paths, etc_php=ROOT / "etc/php", sbin=ROOT / "usr/sbin", usr_bin=ROOT / "usr/bin")
# the extracted packages have no postinst, so create the module ini files that apt normally generates
mods = ROOT / "etc/php/8.3/mods-available"
for so in (ROOT / "usr/lib/php/20230831").glob("*.so"):
    if so.stem != "mbstring":   # needs libonig, which is not in this sandbox
        (mods / f"{so.stem}.ini").write_text(f"; priority=20\n{'zend_extension' if so.stem == 'opcache' else 'extension'}={so}\n")
php.ensure_conf_d("8.3")
for name, _ in php.extensions("8.3"):
    php.set_extension("8.3", name, True)
php.install_composer(__import__("pipanel.core.php", fromlist=["http_fetch"]).http_fetch)

project = T / "app"; project.mkdir()
(project / "artisan").write_text("")
(project / "composer.json").write_text(json.dumps({"name": "x/app", "require": {"php": ">=8.1"}}))
tools = LaravelTools(paths, Runner(), php)
env_home = {"COMPOSER_HOME": str(T / "chome")}
os.environ.update(env_home)
for label, steps in (("require psr/log", tools.install_custom(project, "8.3", "psr/log:^3.0")),
                     ("require --dev psr/clock", tools.install_custom(project, "8.3", "psr/clock", dev=True))):
    print(label, "->", [(n, r.ok) for n, r in steps])
decl = declared_packages(project)
print("declared:", sorted(decl - {"php"}), "| vendor/psr/log:", (project / "vendor/psr/log").is_dir())
from pipanel.core.laravel import Package
steps = tools.remove(project, "8.3", Package("x", "x", "x", ("psr/log",)))
print("remove ->", [(n, r.ok) for n, r in steps], "| still declared:", "psr/log" in declared_packages(project))
bad = tools.install_custom(project, "8.3", "no-such-vendor-xyz/no-such-package-xyz")
print("unknown package ->", bad[0][1].ok, "|", (bad[0][1].text or "").strip().splitlines()[-1][:90])
