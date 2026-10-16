"""ngrok: install the binary, authtoken, share one site, read the public URL from the local API."""
from __future__ import annotations

import hashlib
import io
import json
import platform
import queue
import re
import subprocess
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import Paths
from .runner import Result, Runner
from .templates import valid_site_name

URLS = {
    "amd64": "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz",
    "arm64": "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm64.tgz",
}
API_PORTS = (4040, 4041, 4042)
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_]{10,}$")


class NgrokError(Exception):
    pass


@dataclass
class TokenCheck:
    ok: bool
    message: str = ""


_ERR_CODE = re.compile(r"ERR_NGROK_\d+")
_OK_MARKERS = ("started tunnel", "client session established")
_FAIL_MARKERS = ("command failed", "authentication failed", "terminating with error")


def summarize_failure(lines: list[str]) -> str:
    """The human part of ngrok's error output, e.g. 'authentication failed: ... (ERR_NGROK_105)'."""
    text = [l[len("ERROR:"):].strip() for l in lines if l.startswith("ERROR:")]
    text = [l for l in text if l and not l.startswith("http")]
    code = next((m.group(0) for l in lines for m in [_ERR_CODE.search(l)] if m), "")
    head = next((l for l in text if "failed" in l or "error" in l.lower()), text[0] if text else "")
    head = re.sub(r"\s+", " ", head)
    return f"{head} ({code})".strip() if code and code not in head else (head or " ".join(lines[-2:])[:200])


def fingerprint(token: str) -> str:
    """Short, non-reversible id of a token, so 'verified' can be remembered without storing the token."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]

def arch(machine: str | None = None) -> str:
    m = (machine or platform.machine()).lower()
    if m in ("x86_64", "amd64"):
        return "amd64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    raise NgrokError(f"unsupported architecture: {m}")


def parse_tunnels(payload: str) -> str | None:
    try:
        tunnels = json.loads(payload).get("tunnels", [])
    except (ValueError, AttributeError):
        return None
    urls = [t.get("public_url", "") for t in tunnels if isinstance(t, dict)]
    return next((u for u in urls if u.startswith("https://")), urls[0] if urls else None)


TOKEN_URL = "https://dashboard.ngrok.com/get-started/your-authtoken"
SIGNUP_URL = "https://dashboard.ngrok.com/signup"
_TOKEN_LINE = re.compile(r"^\s*authtoken:\s*[\"']?([^\s\"'#]+)", re.M)


class Ngrok:
    def __init__(self, paths: Paths, runner: Runner, *, home: Path | None = None, config_home: Path | None = None):
        self.paths, self.runner = paths, runner
        home = home or Path.home()
        config_home = config_home or home / ".config"
        # where `ngrok config add-authtoken` writes (v3 on Linux, then the older location)
        self.config_files = [config_home / "ngrok" / "ngrok.yml", home / ".ngrok2" / "ngrok.yml"]

    @property
    def bin(self) -> Path:
        return self.paths.bin / "ngrok"

    def installed(self) -> bool:
        return self.bin.exists()

    def install(self, fetch: Callable[[str], bytes], machine: str | None = None) -> Path:
        data = fetch(URLS[arch(machine)])
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            member = next((m for m in t.getmembers() if m.isfile() and Path(m.name).name == "ngrok"), None)
            if member is None:
                raise NgrokError("archive does not contain the ngrok binary")
            f = t.extractfile(member)
            assert f is not None
            self.paths.bin.mkdir(parents=True, exist_ok=True)
            self.bin.write_bytes(f.read())
        self.bin.chmod(0o755)
        return self.bin

    def set_authtoken(self, token: str) -> Result:
        token = token.strip()
        if not _TOKEN_RE.match(token):
            raise NgrokError("invalid authtoken")
        return self.runner.run([str(self.bin), "config", "add-authtoken", token])

    def read_token(self) -> str:
        for f in self.config_files:
            try:
                m = _TOKEN_LINE.search(f.read_text())
            except OSError:
                continue
            if m:
                return m.group(1)
        return ""

    def has_authtoken(self) -> bool:
        """Is a non-empty authtoken saved? (`ngrok config check` only validates syntax, so read the file.)"""
        for f in self.config_files:
            try:
                if _TOKEN_LINE.search(f.read_text()):
                    return True
            except OSError:
                continue
        return False

    def verify(self, port: int = 80, timeout: float = 25.0, spawn=subprocess.Popen) -> TokenCheck:
        """Really check the saved token: start ngrok briefly and wait until ngrok's servers accept the session
        (a tunnel is created) or reject it. Needs internet. A free account allows one session at a time."""
        if not self.installed():
            raise NgrokError("ngrok is not installed")
        proc = spawn([str(self.bin), "http", str(port), "--log=stdout", "--log-format=logfmt"],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True)
        lines: queue.Queue = queue.Queue()
        def pump() -> None:
            for l in proc.stdout:
                lines.put(l.rstrip("\n"))
            lines.put(None)   # end of output: the process has exited
        threading.Thread(target=pump, daemon=True).start()
        seen: list[str] = []
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=0.5)
                except queue.Empty:
                    continue
                if line is None:   # the process ended on its own
                    return TokenCheck(False, summarize_failure(seen) or "ngrok stopped before connecting")
                seen.append(line)
                if any(m in line for m in _OK_MARKERS) and "ERR_NGROK" not in line and "lvl=eror" not in line:
                    return TokenCheck(True, "ngrok accepted the token")
                if _ERR_CODE.search(line) or any(m in line for m in _FAIL_MARKERS):
                    time.sleep(0.5)   # let the rest of the error text arrive
                    while True:
                        try:
                            more = lines.get_nowait()
                        except queue.Empty:
                            break
                        if more is not None:
                            seen.append(more)
                    return TokenCheck(False, summarize_failure(seen))
            return TokenCheck(False, "Timed out waiting for ngrok. Check your internet connection and try again.")
        finally:
            proc.terminate()
            try:
                proc.wait(3)
            except Exception:  # noqa: BLE001
                proc.kill()

    def unit_spec(self, site: str, tld: str = "test", port: int = 80) -> dict:
        if not valid_site_name(site):
            raise NgrokError(f"invalid site name: {site!r}")
        return dict(description=f"PIPanel: ngrok share {site}",
                    exec_start=f"{self.bin} http {port} --host-header={site}.{tld} --log=stdout")

    def public_url(self, fetch: Callable[[str], bytes]) -> str | None:
        for port in API_PORTS:
            try:
                url = parse_tunnels(fetch(f"http://127.0.0.1:{port}/api/tunnels").decode())
            except OSError:
                continue
            if url:
                return url
        return None
