import os, re, subprocess, sys, time, shutil
from pathlib import Path
import os
os.environ.setdefault("PIPANEL_UNIT_PREFIX", "pipanel-e2e")   # never touch the units of a real installation
REPO = __import__("pathlib").Path(__file__).resolve().parent.parent   # repo root, wherever it is checked out
sys.path.insert(0, str(REPO))
S = Path(sys.argv[1]); ROOT = S / "root"
T = Path("/tmp/dpe2e"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
os.environ["CAROOT"] = str(T / "caroot"); (T / "caroot").mkdir()
from pipanel.core.controller import Controller
from pipanel.core.paths import Paths
from pipanel.core.php import PHP
from pipanel.core.runner import Runner
paths = Paths.under(T); paths.ensure()
php = PHP(paths, etc_php=ROOT/"etc/php", sbin=ROOT/"usr/sbin", usr_bin=ROOT/"usr/bin")
ctl = Controller(paths, Runner(), home=T/"home", php=php)
ctl.services.nginx_bin = str(ROOT/"usr/sbin/nginx"); ctl.services.etc_nginx = ROOT/"etc/nginx"
ctl.services._nginx_version = None
proj = T/"proj"
(proj/"blog/public").mkdir(parents=True); (proj/"blog/artisan").write_text("")
(proj/"blog/public/index.php").write_text('<?php echo "PHP ".PHP_VERSION." uri=".$_SERVER["REQUEST_URI"]." root=".$_SERVER["DOCUMENT_ROOT"];')
(proj/"plain").mkdir(); (proj/"plain/index.php").write_text('<?php echo "plain ".phpversion();')
ctl.cfg.http_port=18080; ctl.cfg.https_port=18443; ctl.park(proj)
cert = ctl.certs.ensure_site_cert("blog"); print("cert:", cert.exists())
ctl.cfg.sites["blog"] = type(ctl.cfg.site("blog"))(php="8.3", https=True); ctl.save()
rep = ctl.apply(); print("apply warnings:", rep.warnings)
units = sorted(paths.systemd_user.glob("pipanel-e2e-*.service")); print("units:", [u.name for u in units])
r = subprocess.run([str(ROOT/"usr/sbin/nginx"), "-p", str(paths.nginx_dir), "-c", str(paths.nginx_dir/"nginx.conf"), "-e", "stderr", "-t"], capture_output=True, text=True)
print("nginx -t:", r.returncode, (r.stdout+r.stderr).strip().splitlines()[-2:])
try:
    for u in units:
        subprocess.run(["systemctl","--user","link",str(u)], capture_output=True)
    subprocess.run(["systemctl","--user","daemon-reload"])
    for s in ("php8.3", "nginx", "dns"):
        res = ctl.services.start(s); print("start", s, res.ok, res.text)
    time.sleep(1.5)
    print("states:", ctl.service_states())
    def curl(*a):
        r = subprocess.run(["curl","-s","-m","5","--resolve","blog.test:18080:127.0.0.1","--resolve","blog.test:18443:127.0.0.1","--resolve","plain.test:18080:127.0.0.1",*a], capture_output=True, text=True)
        return r.stdout.strip() or r.stderr.strip()
    print("http blog   :", curl("http://blog.test:18080/some/route?x=1"))
    print("https blog  :", curl("--cacert", str(T/"caroot/rootCA.pem"), "https://blog.test:18443/"))
    print("http plain  :", curl("http://plain.test:18080/"))
    print("unknown host:", curl("-H", "Host: nope.test", "http://127.0.0.1:18080/"))
    print("dotfile     :", curl("-o","/dev/null","-w","%{http_code}","http://blog.test:18080/.env"))
    # change the site PHP through the controller -> nginx reload
    ctl.set_site_php("blog", "8.3"); time.sleep(0.5)
    print("after reload:", curl("http://blog.test:18080/"))
    print("sock:", paths.php_socket("8.3").exists(), "| logs:", sorted(p.name for p in paths.logs.iterdir()))
finally:
    for s in ("nginx", "php8.3", "dns"):
        subprocess.run(["systemctl","--user","stop",__import__("pipanel.core.services", fromlist=["unit_name"]).unit_name(s)], capture_output=True)
    for u in units:
        subprocess.run(["systemctl","--user","disable",u.name], capture_output=True)
    subprocess.run(["systemctl","--user","daemon-reload"])
