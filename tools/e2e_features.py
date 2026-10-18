"""Real test of the newer features against the system's nginx and PHP-FPM, on unprivileged ports:
proxy sites, Mailpit (downloaded), Adminer (downloaded), a site process (fnm + npm), PHP on demand, and the
per-site `php` wrapper. Uses its own unit prefix and a temp folder, so a real installation is not touched.

    python tools/e2e_features.py
"""
import os
import shutil
import smtplib
import subprocess
import sys
import threading
import time
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ.setdefault("PIPANEL_UNIT_PREFIX", "pipanel-e2e")   # never touch the units of a real installation
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from pipanel.core.controller import Controller   # noqa: E402
from pipanel.core.paths import Paths              # noqa: E402
from pipanel.core.php import http_fetch           # noqa: E402
from pipanel.core.runner import Runner            # noqa: E402

T = Path("/tmp/pipanel-e2e-features")
shutil.rmtree(T, ignore_errors=True)
T.mkdir()
paths = Paths.under(T)
paths.ensure()
ctl = Controller(paths, Runner(), home=T / "home", fetch=http_fetch)
(T / "home").mkdir()
versions = ctl.php.installed()
assert versions, "needs php-fpm installed on this machine"
ctl.cfg.http_port, ctl.cfg.https_port, ctl.cfg.default_php = 18080, 18443, versions[-1]
ctl.cfg.autostart_services = False

# fnm and its Node versions of the real installation (read only), so the Vite-style process can run
real = Paths.from_env()
if (real.bin / "fnm").exists():
    shutil.copy2(real.bin / "fnm", paths.bin / "fnm")
    os.symlink(real.data / "fnm", paths.data / "fnm")

proj = T / "proj"
blog = proj / "blog"
(blog / "public").mkdir(parents=True)
(blog / "artisan").write_text("")
(blog / "public" / "index.php").write_text('<?php echo "PHP ".PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;')
(blog / "package.json").write_text('{"scripts": {"dev": "node -e \\"setInterval(() => {}, 1000)\\""}}')
ctl.park(proj)
if len(versions) > 1:   # one site on an older version: both versions must run, the others must not
    ctl.cfg.sites["blog"] = type(ctl.cfg.site("blog"))(php=versions[0])
ctl.save()


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"upstream host={self.headers['Host']} fwd={self.headers['X-Forwarded-Host']}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


up = HTTPServer(("127.0.0.1", 18999), Upstream)
threading.Thread(target=up.serve_forever, daemon=True).start()


def curl(host, path="/", *extra):
    r = subprocess.run(["curl", "-s", "-m", "8", "--resolve", f"{host}:18080:127.0.0.1", *extra,
                        f"http://{host}:18080{path}"], capture_output=True, text=True)
    return r.stdout.strip() or r.stderr.strip()


def link_units():
    """The units live in the temp folder, so systemd only knows them once they are linked."""
    for u in paths.systemd_user.glob("pipanel-e2e-*.service"):
        subprocess.run(["systemctl", "--user", "link", str(u)], capture_output=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"])


_write_extra_unit = ctl.services.write_extra_unit


def write_and_link(name, **spec):
    changed = _write_extra_unit(name, **spec)
    link_units()
    return changed


ctl.services.write_extra_unit = write_and_link


def wait_for(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        time.sleep(0.2)
    return cond()


ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


try:
    ctl.add_proxy("app", "18999")
    print("installing Mailpit and Adminer (downloads)…")
    ctl.mailpit.install(ctl.fetch)
    ctl.services.write_extra_unit("mail", **ctl.mailpit.unit_spec())
    ctl.install_adminer()
    ctl.apply()
    link_units()
    for s in ["nginx", *[f"php{v}" for v in ctl.php_in_use()], "mail"]:
        r = ctl.services.start(s)
        check(f"start {s}", r.ok, r.text)
    # PHP-FPM can take a few seconds before its socket exists
    check("PHP-FPM sockets appear", wait_for(lambda: all(paths.php_socket(v).exists() for v in ctl.php_in_use())))
    time.sleep(0.5)

    check("site runs its own PHP version", curl("blog.test") == f"PHP {ctl.site('blog').php}", curl("blog.test"))
    out = curl("app.test", "/x")
    check("proxy forwards with the upstream host and X-Forwarded-Host",
          out == "upstream host=127.0.0.1:18999 fwd=app.test", out)

    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "app@blog.test", "user@example.com", "PIPanel e2e"
    msg.set_content("hello")
    with smtplib.SMTP("127.0.0.1", 1025, timeout=5) as s:
        s.send_message(msg)
    time.sleep(0.5)
    out = curl("mailpit.test", "/api/v1/messages")
    check("Mailpit caught the email and its inbox is served at mailpit.test", "PIPanel e2e" in out, out[:200])
    lan = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
    if lan:
        r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-H", "Host: mailpit.test",
                            f"http://{lan[0]}:18080/"], capture_output=True, text=True)
        check("mailpit.test refuses other computers", r.stdout == "403", r.stdout)

    jar = str(T / "cookies")   # Adminer signs in with a POST, then redirects to the page with a session cookie
    out = curl("adminer.test", "/", "-L", "-c", jar, "-b", jar)
    check("Adminer renders and signs in on its own", "Select database" in out and "without a password" not in out,
          out[:300])
    import re
    err = re.search(r"<div class='error'>(.*?)</div>", out)
    print("      Adminer sign-in result:", err.group(1)[:160] if err else "signed in")

    print("installing phpMyAdmin (download)…")
    ctl.install_phpmyadmin()
    pma_php = ctl._internal(ctl.sites())[0][-1].php
    wait_for(lambda: paths.php_socket(pma_php).exists())
    wait_for(lambda: "site not found" not in curl("phpmyadmin.test"), 5)
    out = curl("phpmyadmin.test", "/", "-L", "-c", jar, "-b", jar)
    check(f"phpMyAdmin (PHP {pma_php}) renders and signs in on its own",
          "via TCP/IP" in out and "Access denied" not in out, out[:300])   # the server panel of a signed-in page

    if ctl.node.available():
        ctl.start_process("blog", "vite")
        time.sleep(1)
        st = ctl.process_states("blog")
        check("site process (npm run dev through fnm) keeps running", st.get("vite") == "active", str(st))
        ctl.stop_process("blog", "vite")
    else:
        print("SKIP  site process: fnm is not installed")

    if len(versions) > 1:
        st = ctl.services.status([f"php{v}" for v in versions])
        want = set(ctl.php_in_use())
        check("only the PHP versions in use run",
              all((s == "active") == (k[3:] in want) for k, s in st.items()), str(st))

    if os.environ.get("E2E_TUNNEL") == "1":   # needs internet; a public URL exists for a few seconds
        ctl.set_share_provider("cloudflare")
        ctl.share("blog")
        url = None
        wait_for(lambda: bool(ctl.share_url()), 30)
        url = ctl.share_url()
        body = ""
        for _ in range(20):   # the new name needs a moment before Cloudflare's DNS knows it
            # DNS over HTTPS: the local resolver would cache the "not found" answer from before the name existed
            r = subprocess.run(["curl", "-s", "-m", "10", "--doh-url", "https://1.1.1.1/dns-query", url or "http://invalid"],
                               capture_output=True, text=True)
            body = r.stdout.strip()
            if body.startswith("PHP"):
                break
            time.sleep(2)
        check("Cloudflare quick tunnel serves the site publicly", body == f"PHP {ctl.site('blog').php}",
              f"{url}: {body[:120]}")
        ctl.stop_share()

    if os.environ.get("E2E_WORDPRESS") == "1":   # downloads WP-CLI and WordPress; a throwaway MariaDB on 13306
        from pipanel.core.mysql import MySQL
        ctl.cfg.mysql_port, ctl.cfg.db_engine = 13306, "mariadb"
        ctl.mysql = MySQL(paths, ctl.runner, port=13306, engine="mariadb")
        ctl.mysql.initialize()
        db_proc = subprocess.Popen(["/usr/sbin/mariadbd", f"--defaults-file={ctl.mysql.cnf}"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            check("throwaway MariaDB is ready", ctl.mysql.wait_ready(30))
            ctl.mysql.drop_extra_root_accounts()
            ctl.mysql.set_root_password("e2e pa'ss")
            print("creating a WordPress site (downloads WP-CLI and WordPress)…")
            site = ctl.new_wordpress("press")
            wait_for(lambda: paths.php_socket(ctl.site("press").php).exists())
            wait_for(lambda: "site not found" not in curl("press.test"), 5)   # nginx reloads in the background
            home = curl("press.test")
            check("WordPress front page renders", "<title>press</title>" in home, home[:200])
            r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{redirect_url}",
                                "--resolve", "press.test:18080:127.0.0.1", "http://press.test:18080/wp-admin/"],
                               capture_output=True, text=True)
            check("wp-admin asks to sign in", "wp-login.php" in r.stdout, r.stdout)
            jar = str(T / "wp-cookies")
            subprocess.run(["curl", "-s", "-c", jar, "--resolve", "press.test:18080:127.0.0.1",
                            "http://press.test:18080/wp-login.php"], capture_output=True)
            r = subprocess.run(["curl", "-s", "-b", jar, "-c", jar, "-o", "/dev/null", "-w", "%{redirect_url}",
                                "--resolve", "press.test:18080:127.0.0.1", "--data-urlencode", "log=admin",
                                "--data-urlencode", f"pwd={site.admin_password}", "--data-urlencode", "testcookie=1",
                                "http://press.test:18080/wp-login.php"], capture_output=True, text=True)
            check("the admin password that PIPanel shows signs in", "wp-admin" in r.stdout, r.stdout)
            check("the backup tool works on that database",
                  ctl.mysql.dump("press", T / "press.sql.gz").stat().st_size > 1000
                  if Path(ctl.mysql.engine.dump).exists() else True)
        finally:
            db_proc.terminate()
            db_proc.wait(20)

    ctl.php.write_cli_wrapper(ctl.cfg.default_php)
    r = subprocess.run([str(paths.bin / "php"), "-r", "echo PHP_MAJOR_VERSION.'.'.PHP_MINOR_VERSION;"],
                       cwd=blog / "public", capture_output=True, text=True)
    check("php in a site folder runs the site's version", r.stdout == ctl.site("blog").php, r.stdout + r.stderr)
finally:
    ctl.stop_all()
    for u in paths.systemd_user.glob("pipanel-e2e-*.service"):
        subprocess.run(["systemctl", "--user", "disable", u.name], capture_output=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    up.shutdown()

print("\nALL PASSED" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
