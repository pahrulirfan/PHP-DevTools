import os, sys, shutil, subprocess
from pathlib import Path
REPO = __import__("pathlib").Path(__file__).resolve().parent.parent   # repo root, wherever it is checked out
sys.path.insert(0, str(REPO))
ROOT = Path(sys.argv[1]) / "root"
T = Path("/tmp/dpe2e2"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
from pipanel.core.controller import Controller
from pipanel.core.paths import Paths
from pipanel.core.php import PHP
from pipanel.core.runner import Runner
paths = Paths.under(T); paths.ensure()
php = PHP(paths, etc_php=ROOT/"etc/php", sbin=ROOT/"usr/sbin", usr_bin=ROOT/"usr/bin")
(T/"home").mkdir()
ctl = Controller(paths, Runner(), home=T/"home", php=php)
print("php cli bin exists:", php.cli_bin("8.3").exists())
php.write_cli_wrapper("8.3")
try:
    print("composer:", ctl.install_composer())
    r = subprocess.run([str(paths.bin/"composer"), "--version"], capture_output=True, text=True, env={**os.environ, "PHPRC": ""})
    print("composer --version:", r.returncode, (r.stdout + r.stderr).strip()[:150])
except Exception as e:
    print("composer ERR:", repr(e))
try:
    ctl.install_fnm(); print("fnm:", ctl.node.available(), ctl.node._fnm("--version").text)
    print("remote:", ctl.node.remote()[:6])
    r = ctl.node.install("22"); print("node install:", r.ok, r.text[-120:])
    print("set default:", ctl.node.set_default("22").ok, "| installed:", ctl.node.installed())
    print("shell block bashrc:\n", (T/"home/.bashrc").read_text())
    out = subprocess.run(["bash", "-ic", "node -v; which node"], capture_output=True, text=True, env={**os.environ, "HOME": str(T/"home")})
    print("bash -i:", out.stdout.strip().replace("\n"," | "), out.stderr.strip()[:200])
except Exception as e:
    print("fnm ERR:", repr(e))
try:
    ctl.ngrok.install(ctl.fetch); r = subprocess.run([str(ctl.ngrok.bin), "version"], capture_output=True, text=True)
    print("ngrok:", r.stdout.strip(), r.stderr.strip()[:100])
except Exception as e:
    print("ngrok ERR:", repr(e))
