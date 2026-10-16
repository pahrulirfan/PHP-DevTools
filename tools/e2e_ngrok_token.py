"""Real check of ngrok token verification against ngrok's servers (needs internet; no valid token is required).

Runs the real `Ngrok.verify` with (1) no token and (2) a made-up token, in an isolated HOME, and prints how ngrok
answered. The success path (a valid token) cannot be tested without an account.
"""
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from pipanel.core.ngrok import Ngrok
from pipanel.core.paths import Paths
from pipanel.core.php import http_fetch
from pipanel.core.runner import Runner

T = Path("/tmp/dpe2e5"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
home = T / "home"; home.mkdir()
os.environ["HOME"] = str(home); os.environ["XDG_CONFIG_HOME"] = str(home / ".config")   # never the real config
paths = Paths.under(T); paths.ensure()
ng = Ngrok(paths, Runner(), home=home, config_home=home / ".config")
ng.install(http_fetch)
print("1) no token       ->", ng.verify(timeout=30))
cfg = ng.config_files[0]; cfg.parent.mkdir(parents=True, exist_ok=True)
cfg.write_text('version: "3"\nagent:\n    authtoken: 2abcDEFghi_1234567890madeUpTokenXYZ\n')
print("2) made-up token  ->", ng.verify(timeout=30))
shutil.rmtree(T, ignore_errors=True)
