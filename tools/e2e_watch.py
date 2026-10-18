"""Real test of the folder watcher: with the app closed, a new folder in the projects folder is served as
name.test within seconds, a Laravel project that is still being unpacked switches to its public/ folder, and a
removed folder stops being served. Uses its own unit prefix, temp folders, and port 18080.

    python tools/e2e_watch.py
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("PIPANEL_UNIT_PREFIX", "pipanel-e2e")   # never touch the units of a real installation
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

T = Path("/tmp/pipanel-e2e-watch")
shutil.rmtree(T, ignore_errors=True)
env = {"HOME": str(T / "home"), "XDG_CONFIG_HOME": str(T / "config"), "XDG_DATA_HOME": str(T / "data"),
       "XDG_STATE_HOME": str(T / "state"), "XDG_RUNTIME_DIR": str(T / "run")}
for v in env.values():
    Path(v).mkdir(parents=True, exist_ok=True)

from pipanel.core.controller import Controller   # noqa: E402
from pipanel.core.paths import Paths              # noqa: E402
from pipanel.core.runner import Runner            # noqa: E402

paths = Paths.from_env(env)   # the XDG layout of a real installation, inside the temp folder
paths.ensure()
ctl = Controller(paths, Runner(), home=T / "home")
ctl.cfg.http_port, ctl.cfg.https_port = 18080, 18443
ctl.cfg.default_php = ctl.php.installed()[-1]
proj = T / "PHPProject"
(proj / "first").mkdir(parents=True)
(proj / "first" / "index.php").write_text('<?php echo "first ok";')
ctl.park(proj)

ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


def curl(host):
    r = subprocess.run(["curl", "-s", "-m", "5", "--resolve", f"{host}:18080:127.0.0.1", f"http://{host}:18080/"],
                       capture_output=True, text=True)
    return r.stdout.strip()


def wait_for(cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.3)
    return False


def link_units():
    for u in paths.systemd_user.glob("pipanel-e2e-*"):
        subprocess.run(["systemctl", "--user", "link", str(u)], capture_output=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"])


try:
    ctl.apply()
    link_units()
    for s in ["nginx", *[f"php{v}" for v in ctl.php_in_use()]]:
        ctl.services.start(s)
    subprocess.run(["systemctl", "--user", "restart", "pipanel-e2e-watch.path"])
    check("PHP-FPM ready", wait_for(lambda: all(paths.php_socket(v).exists() for v in ctl.php_in_use())))
    check("the parked site works", wait_for(lambda: curl("first.test") == "first ok"), curl("first.test"))
    st = subprocess.run(["systemctl", "--user", "is-active", "pipanel-e2e-watch.path"], capture_output=True,
                        text=True).stdout.strip()
    check("the folder watcher runs", st == "active", st)

    # --- the app is not involved from here on: only the folder changes ---
    t0 = time.monotonic()
    (proj / "fresh").mkdir()
    (proj / "fresh" / "index.php").write_text('<?php echo "fresh ok";')
    served = wait_for(lambda: curl("fresh.test") == "fresh ok")
    check(f"a new folder is served by itself ({time.monotonic() - t0:.1f}s)", served, curl("fresh.test"))

    # a Laravel project that is still being created: first an empty folder, files arrive a few seconds later
    (proj / "shop").mkdir()
    wait_for(lambda: (paths.nginx_sites / "shop.conf").exists())
    time.sleep(4)
    (proj / "shop" / "public").mkdir()
    (proj / "shop" / "public" / "index.php").write_text('<?php echo "laravel public ok";')
    (proj / "shop" / "artisan").write_text("")
    served = wait_for(lambda: curl("shop.test") == "laravel public ok", 30)
    check("a project that is still being unpacked switches to public/", served, curl("shop.test"))

    shutil.rmtree(proj / "fresh")
    gone = wait_for(lambda: not (paths.nginx_sites / "fresh.conf").exists()
                    and "site not found" in curl("fresh.test"))   # nginx reloads in the background
    check("a removed folder stops being served", gone, curl("fresh.test"))
finally:
    ctl.stop_all()
    subprocess.run(["systemctl", "--user", "stop", "pipanel-e2e-watch.path"], capture_output=True)
    for u in paths.systemd_user.glob("pipanel-e2e-*"):
        subprocess.run(["systemctl", "--user", "disable", u.name], capture_output=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    log = subprocess.run(["journalctl", "--user", "-u", "pipanel-e2e-watch.service", "--since", "-5 min",
                          "--no-pager", "-o", "cat"], capture_output=True, text=True).stdout
    print("\nwatcher log (last lines):\n  " + "\n  ".join(log.strip().splitlines()[-6:]))

print("\nALL PASSED" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
