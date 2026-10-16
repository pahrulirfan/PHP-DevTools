import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from pipanel.core import dns, templates as T
from pipanel.core.certs import Certs, CertError
from pipanel.core.runner import Result, Runner


def test_ensure_cert_calls_mkcert(paths, runner):
    c = Certs(paths, runner)
    cert, key = c.cert_paths("blog", "test")

    def fake(argv, **kw):
        cert.write_text("c"); key.write_text("k")
        return Result(0)
    runner.run = fake
    assert c.ensure_site_cert("blog") == cert
    assert oct(key.stat().st_mode & 0o777) == "0o600" and c.has_cert("blog", "test")
    c.remove_site_cert("blog")
    assert not c.has_cert("blog", "test")


def test_ensure_cert_failure_and_bad_name(paths, runner):
    c = Certs(paths, runner)
    runner.on(["mkcert"], Result(1, "", "boom"))
    with pytest.raises(CertError, match="boom"):
        c.ensure_site_cert("blog")
    with pytest.raises(CertError):
        c.ensure_site_cert("../x")


def test_install_ca_uses_nss_only(paths, runner):
    Certs(paths, runner).install_ca_user()
    assert runner.last_kw["env"] == {"TRUST_STORES": "nss"}


@pytest.mark.skipif(not shutil.which("mkcert"), reason="mkcert is not installed")
def test_real_mkcert_generates_cert(paths, tmp_path, monkeypatch):
    monkeypatch.setenv("CAROOT", str(tmp_path / "caroot"))
    (tmp_path / "caroot").mkdir()
    c = Certs(paths, Runner())
    cert = c.ensure_site_cert("blog")
    assert "BEGIN CERTIFICATE" in cert.read_text()
    assert "PRIVATE KEY" in c.cert_paths("blog", "test")[1].read_text()


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(not Path("/usr/sbin/dnsmasq").exists(), reason="dnsmasq is not installed")
def test_real_dnsmasq_answers_test_domain(paths):
    port = _free_port()
    conf = paths.dns_dir / "dnsmasq.conf"
    conf.write_text(T.render_dnsmasq(paths, port=port))
    proc = subprocess.Popen(["/usr/sbin/dnsmasq", "--keep-in-foreground", f"--conf-file={conf}"],
                            stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(30):
            if dns.query_a("blog.test", port=port, timeout=0.3) or proc.poll() is not None:
                break
            time.sleep(0.1)
        assert proc.poll() is None, proc.stderr.read()
        assert dns.query_a("blog.test", port=port) == ["127.0.0.1"]
        assert dns.query_a("deep.sub.blog.test", port=port) == ["127.0.0.1"]
        assert dns.query_a("example.com", port=port) == []   # does not answer other domains
    finally:
        proc.terminate(); proc.wait(5)


def test_query_a_no_server_returns_empty():
    assert dns.query_a("x.test", port=_free_port(), timeout=0.2) == []


def test_https_without_mkcert_gives_a_clear_message_not_a_crash(paths, runner):
    runner.on(["mkcert"], Result(127, "", "command not found: mkcert"))
    with pytest.raises(CertError, match="needs mkcert.*sudo apt install mkcert"):
        Certs(paths, runner).ensure_site_cert("blog")
