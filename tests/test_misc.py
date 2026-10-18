import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from pipanel.core import autostart, logs, projects
from pipanel.core.actions import terminal_cmd
from pipanel.core.ngrok import Ngrok, NgrokError, parse_tunnels
from pipanel.core.runner import Result, Runner


# ---------- ngrok ----------
def _tgz(names):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as t:
        for n in names:
            info = tarfile.TarInfo(n); data = b"BIN"; info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return b.getvalue()


@pytest.mark.posix
def test_ngrok_install_and_unit(paths, runner):
    n = Ngrok(paths, runner)
    n.install(lambda u: _tgz(["ngrok"]), "x86_64")
    assert n.installed() and n.bin.stat().st_mode & 0o100
    spec = n.unit_spec("blog")
    assert spec["argv"] == [str(n.bin), "http", "80", "--host-header=blog.test", "--log=stdout"]
    with pytest.raises(NgrokError):
        n.unit_spec("a;b")
    with pytest.raises(NgrokError):
        n.install(lambda u: _tgz(["readme"]), "x86_64")


def test_ngrok_token(paths, runner):
    n = Ngrok(paths, runner)
    n.set_authtoken("2abcDEF_1234567890")
    assert runner.calls[-1][-2:] == ["add-authtoken", "2abcDEF_1234567890"]
    for bad in ("short", "a b c d e f g h i j", "x;ls -la /tmp/xxxxx"):
        with pytest.raises(NgrokError):
            n.set_authtoken(bad)


def test_parse_tunnels():
    p = json.dumps({"tunnels": [{"public_url": "http://a.ngrok.app"}, {"public_url": "https://a.ngrok.app"}]})
    assert parse_tunnels(p) == "https://a.ngrok.app"
    assert parse_tunnels("{}") is None and parse_tunnels("garbage") is None


def test_ngrok_public_url_via_real_http(paths, runner, monkeypatch):
    import urllib.request
    import pipanel.core.ngrok as ng

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"tunnels": [{"public_url": "https://x.ngrok.app"}]}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fetch = lambda url: urllib.request.urlopen(url, timeout=2).read()
    try:
        monkeypatch.setattr(ng, "API_PORTS", (1, srv.server_address[1]))   # port 1 is refused -> try the next one
        assert Ngrok(paths, runner).public_url(fetch) == "https://x.ngrok.app"
        monkeypatch.setattr(ng, "API_PORTS", (1,))
        assert Ngrok(paths, runner).public_url(fetch) is None
    finally:
        srv.shutdown()


# ---------- logs ----------
def test_tail_large_file(tmp_path):
    f = tmp_path / "big.log"
    f.write_text("\n".join(f"line{i}" for i in range(20000)) + "\n")
    out = logs.tail(f, 5)
    assert out.splitlines() == [f"line{i}" for i in range(19995, 20000)]
    assert logs.tail(tmp_path / "nope.log") == ""
    f.write_text("a\nb\n")
    assert logs.tail(f, 10) == "a\nb"


def test_log_sources_and_journal(paths, runner):
    srcs = logs.sources(paths, ["blog"], ["8.3"])
    labels = [s.label for s in srcs]
    assert "PHP-FPM 8.3" in labels and "blog (access)" in labels and "Nginx (error)" in labels
    runner.on(["journalctl"], Result(0, "entry\n"))
    assert logs.read(srcs[0], runner) == "entry\n"
    assert runner.calls[-1][:5] == ["journalctl", "--user", "-u", "pipanel-nginx.service", "-n"]


# ---------- autostart ----------
def test_autostart(tmp_path):
    assert not autostart.enabled(tmp_path)
    p = autostart.enable(tmp_path, "/x/pipanel --minimized")
    assert "Exec=/x/pipanel --minimized" in p.read_text() and autostart.enabled(tmp_path)
    autostart.disable(tmp_path)
    assert not autostart.enabled(tmp_path)


# ---------- projects ----------
def test_set_env_updates_and_appends(tmp_path):
    (tmp_path / ".env.example").write_text("APP_NAME=Laravel\nDB_CONNECTION=sqlite\n# DB_HOST=127.0.0.1\n")
    projects.set_env(tmp_path, projects.mysql_env(3306, "app", 'p w"$'))
    t = (tmp_path / ".env").read_text()
    assert "DB_CONNECTION=mysql" in t and "DB_HOST=127.0.0.1" in t and "# DB_HOST" not in t
    assert 'DB_PASSWORD="p w\\"\\$"' in t and "APP_NAME=Laravel" in t
    assert t.count("DB_CONNECTION") == 1


def test_create_laravel_validation(paths, runner, tmp_path):
    for bad in ("../x", "a b", "", "-x"):
        with pytest.raises(projects.ProjectError):
            projects.create_laravel(runner, paths, tmp_path, bad)
    with pytest.raises(projects.ProjectError, match="Composer"):
        projects.create_laravel(runner, paths, tmp_path, "ok")
    (paths.bin / "composer").write_text("")
    projects.create_laravel(runner, paths, tmp_path, "ok")
    assert runner.calls[-1][1:4] == ["create-project", "laravel/laravel", "ok"]
    assert runner.last_kw["cwd"] == str(tmp_path) and runner.last_kw["env"]["PATH"].startswith(str(paths.bin))
    (tmp_path / "ok").mkdir()
    with pytest.raises(projects.ProjectError, match="already exists"):
        projects.create_laravel(runner, paths, tmp_path, "ok")


# ---------- actions ----------
@pytest.mark.posix
def test_terminal_cmd():
    p = Path("/x")
    assert terminal_cmd(p, which=lambda t: t if t == "gnome-terminal" else None) == ["gnome-terminal", "--working-directory=/x"]
    assert terminal_cmd(p, which=lambda t: None) is None


# ---------- downloads ----------
def test_http_fetch_resumes_a_dropped_download():
    from pipanel.core.php import http_fetch
    payload = bytes(range(256)) * 400
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            rng = self.headers.get("Range")
            seen.append(rng)
            start = int(rng.split("=")[1].rstrip("-")) if rng else 0
            self.send_response(206 if rng else 200)
            self.send_header("Content-Length", str(len(payload) - start))
            self.end_headers()
            part = payload[start:start + 30000] if len(seen) < 3 else payload[start:]
            self.wfile.write(part)   # the first two answers stop early, like a dropped connection
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert http_fetch(f"http://127.0.0.1:{srv.server_port}/f") == payload
    finally:
        srv.shutdown()
    assert seen == [None, "bytes=30000-", "bytes=60000-"]


# ---------- PIPanel's own DNS server (Windows, macOS) ----------
def test_dns_server_answers_test_names():
    import socket as _s
    from pipanel.core import dns, dnsd
    with _s.socket(_s.AF_INET, _s.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    ready = threading.Event()
    threading.Thread(target=dnsd.serve, args=(port, "test"), kwargs={"ready": ready.set}, daemon=True).start()
    assert ready.wait(5)
    assert dns.query_a("blog.test", port=port) == ["127.0.0.1"]
    assert dns.query_a("deep.sub.blog.test", port=port) == ["127.0.0.1"]
    assert dns.query_a("example.com", port=port) == []


def test_dns_answer_records():
    import struct
    from pipanel.core.dnsd import AAAA, answer, parse_question

    def query(name, qtype, tid=0x1234):
        q = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
        for label in name.split("."):
            q += bytes([len(label)]) + label.encode()
        return q + b"\x00" + struct.pack(">HH", qtype, 1)
    assert parse_question(query("Blog.TEST", 1))[1:3] == ("blog.test", 1)
    r = answer(query("blog.test", 1))
    assert r[:2] == b"\x12\x34" and struct.unpack(">H", r[6:8])[0] == 1 and r.endswith(bytes([127, 0, 0, 1]))
    r = answer(query("blog.test", AAAA))
    assert struct.unpack(">H", r[6:8])[0] == 0 and struct.unpack(">H", r[2:4])[0] & 0xF == 0   # NOERROR, empty
    assert struct.unpack(">H", answer(query("example.com", 1))[2:4])[0] & 0xF == 5            # REFUSED
    assert answer(b"\x00" * 5) is None
    assert answer(query("blog.test", 1)[:-3]) is None
