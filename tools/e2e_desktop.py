"""Real test of PIPanel on Windows and macOS (GitHub Actions runners): the first-time setup with the real
downloads or Homebrew, the agent running the services, *.test through the system resolver, a PHP version per
site, the `php` command following the site, MariaDB, Mailpit and Redis.

    python tools/e2e_desktop.py

Everything lives in a temp PIPANEL_HOME. It changes the machine's DNS rule for .test and trusts mkcert's CA,
so run it on a throwaway machine (CI), not on your own.
"""
import json
import os
import smtplib
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path

HOME = Path(tempfile.mkdtemp(prefix="pipanel-e2e-"))
os.environ["PIPANEL_HOME"] = str(HOME)
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from pipanel.core import platform  # noqa: E402
from pipanel.core.controller import Controller  # noqa: E402
from pipanel.core.php import http_fetch  # noqa: E402

ok = True
results = []


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    line = f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{str(detail)[:400]}]" if detail and not cond else "")
    print(line, flush=True)
    results.append(line)
    if not cond and os.environ.get("GITHUB_ACTIONS"):
        print(f"::error title=e2e: {name}::{str(detail)[:900]}", flush=True)


def wait_for(cond, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


def get(url, host=None):
    req = urllib.request.Request(url, headers={"Host": host} if host else {})
    with urllib.request.urlopen(req, timeout=10) as r:   # noqa: S310
        return r.read().decode("utf-8", "replace")


ctl = Controller(fetch=http_fetch)
ctl.cfg.http_port, ctl.cfg.https_port = 18080, 18443
ctl.cfg.autostart_services = False
ctl.save()
print(f"platform={platform.current()} home={HOME} php={ctl.cfg.default_php} dns_port={ctl.cfg.dns_port}", flush=True)

try:
    t0 = time.monotonic()
    steps = ctl.setup()
    for name, r in steps:
        check(f"setup: {name}", r.ok, r.text)
    print(f"setup took {time.monotonic() - t0:.0f}s", flush=True)

    proj = HOME / "Projects"
    for name, code in (("blog", '<?php echo "PHP ".PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;'),
                       ("legacy", '<?php echo "PHP ".PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;')):
        (proj / name / "public").mkdir(parents=True)
        (proj / name / "artisan").write_text("")
        (proj / name / "public" / "index.php").write_text(code)
    ctl.park(proj)
    ctl.apply()
    r = ctl.start_all()
    check("start all services", r.ok, r.text)
    states = ctl.service_states()
    print("states:", states, flush=True)
    url = "http://blog.test:18080/"
    check("a site answers through Nginx and PHP (Host header)",
          wait_for(lambda: get("http://127.0.0.1:18080/", "blog.test") == f"PHP {ctl.cfg.default_php}"),
          states)
    check("blog.test resolves through the system and serves the site",
          wait_for(lambda: get(url) == f"PHP {ctl.cfg.default_php}", 20))
    try:
        get("http://127.0.0.1:18080/", "nope.test")
        unknown = ""
    except urllib.error.HTTPError as e:
        unknown = e.read().decode()
    check("an unknown site gets PIPanel's page", "site not found" in unknown, unknown)

    # a second PHP version for one site: installed, started on demand, and used by the terminal there
    other = "8.3"
    r = ctl.install_php(other)
    check(f"install PHP {other}", r.ok, r.text)
    ctl.set_site_php("legacy", other)
    check(f"legacy.test runs PHP {other}",
          wait_for(lambda: get("http://127.0.0.1:18080/", "legacy.test") == f"PHP {other}"))
    check("blog.test still runs the default", get("http://127.0.0.1:18080/", "blog.test") == f"PHP {ctl.cfg.default_php}")
    php_cmd = [*platform.self_command(), "_run", "php", "-r", "echo PHP_MAJOR_VERSION.'.'.PHP_MINOR_VERSION;"]
    out = subprocess.run(php_cmd, cwd=proj / "legacy", capture_output=True, text=True).stdout.strip()
    check("`php` in a site folder runs the site's version", out == other, out)

    # database
    steps = ctl.install_database()
    for name, r in steps:
        check(f"database: {name}", r.ok, r.text)
    check("the database answers", wait_for(ctl.mysql.ping, 30))
    ctl.mysql.create_database("e2e_shop")
    check("a database can be created", "e2e_shop" in ctl.mysql.databases())

    # Mailpit
    r = ctl.install_mailpit()
    check("Mailpit starts", r.ok, r.text)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "app@blog.test", "user@example.com", "PIPanel desktop e2e"
    msg.set_content("hello")

    def send():
        with smtplib.SMTP("127.0.0.1", 1025, timeout=5) as s:
            s.send_message(msg)
        return True
    check("Mailpit takes an email", wait_for(send, 20))
    check("the inbox shows it", wait_for(lambda: "PIPanel desktop e2e" in get("http://127.0.0.1:18080/api/v1/messages",
                                                                             "mailpit.test"), 20))

    # Redis
    steps = ctl.install_redis()
    for name, r in steps:
        check(f"redis: {name}", r.ok, r.text)
    check("Redis answers PING", wait_for(ctl.redis.ping, 20))

    print("final states:", ctl.service_states(), flush=True)
finally:
    try:
        ctl.stop_all()
        st = json.loads((ctl.paths.state / "agent" / "status.json").read_text())
        from pipanel.core.agent import kill_tree
        kill_tree(int(st["pid"]))
    except Exception as e:  # noqa: BLE001
        print("cleanup:", e)
    logs = ctl.paths.logs / "units"
    if not ok and logs.is_dir():
        for f in sorted(logs.glob("*.log")):
            print(f"--- {f.name}\n" + f.read_text(errors="replace")[-1500:], flush=True)

print("\n" + ("ALL PASSED" if ok else "SOME CHECKS FAILED"))
sys.exit(0 if ok else 1)
