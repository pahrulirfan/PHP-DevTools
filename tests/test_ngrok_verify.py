import io

import pytest

from pipanel.core.controller import Controller, ControllerError
from pipanel.core.ngrok import Ngrok, TokenCheck, fingerprint, summarize_failure
from pipanel.core.runner import Result

OK_LOG = ['t=2026-10-16T22:00:00+0800 lvl=info msg="open config file" path=/x',
          't=2026-10-16T22:00:02+0800 lvl=info msg="client session established" obj=tunnels',
          't=2026-10-16T22:00:02+0800 lvl=info msg="started tunnel" obj=tunnels name=command_line addr=http://localhost:80 url=https://a.ngrok-free.app']
BAD_LOG = ['t=2026-10-16T22:00:00+0800 lvl=info msg="open config file" path=/x',
           't=2026-10-16T22:00:03+0800 lvl=crit msg="command failed" err="authentication failed: The authtoken you specified is invalid."',
           'ERROR:  authentication failed: The authtoken you specified is invalid.',
           'ERROR:  ',
           'ERROR:  Get your credential: https://dashboard.ngrok.com/get-started/your-authtoken',
           'ERROR:  ',
           'ERROR:  ERR_NGROK_105']


class FakeProc:
    def __init__(self, lines, exits=True):
        self.stdout = io.StringIO("".join(l + "\n" for l in lines))
        self.terminated = False

    def terminate(self): self.terminated = True
    def wait(self, t=None): return 0
    def kill(self): pass


@pytest.fixture
def ng(paths, runner, tmp_path):
    n = Ngrok(paths, runner, home=tmp_path / "home", config_home=tmp_path / "home" / ".config")
    n.bin.write_text("")
    return n


def verify(ng, lines):
    procs = []
    res = ng.verify(timeout=5, spawn=lambda *a, **k: procs.append(FakeProc(lines)) or procs[-1])
    return res, procs[0]


def test_verified_when_ngrok_starts_a_tunnel(ng):
    res, proc = verify(ng, OK_LOG)
    assert res.ok and proc.terminated      # the probe process is always stopped again


def test_rejected_token_returns_ngroks_own_message(ng):
    res, proc = verify(ng, BAD_LOG)
    assert not res.ok and proc.terminated
    assert "authtoken you specified is invalid" in res.message and "ERR_NGROK_105" in res.message
    assert "dashboard.ngrok.com" not in res.message     # no URL noise


def test_process_that_exits_silently_is_a_failure(ng):
    res, _ = verify(ng, ['t=... lvl=info msg="open config file"'])
    assert not res.ok and res.message


def test_summarize_failure_handles_the_missing_token_case():
    lines = ["ERROR:  authentication failed: This ngrok session is not authenticated. ngrok requires an account "
             "and a valid credential to start a session.", "ERROR:  ", "ERROR:  ERR_NGROK_4018"]
    msg = summarize_failure(lines)
    assert "not authenticated" in msg and msg.endswith("(ERR_NGROK_4018)")


def test_verify_needs_the_binary(paths, runner, tmp_path):
    n = Ngrok(paths, runner, home=tmp_path / "h", config_home=tmp_path / "h" / ".config")
    with pytest.raises(Exception, match="not installed"):
        n.verify()


def test_read_token_and_fingerprint(ng):
    f = ng.config_files[0]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('version: "3"\nagent:\n    authtoken: 2abcDEF_1234567890\n')
    assert ng.read_token() == "2abcDEF_1234567890"
    assert fingerprint("a") != fingerprint("b") and len(fingerprint("a")) == 16 and "2abc" not in fingerprint("2abcDEF")


# ---------- controller: the gate ----------
@pytest.fixture
def ctl(paths, runner, tmp_path):
    c = Controller(paths, runner, home=tmp_path / "home", fetch=lambda u: b"")
    (tmp_path / "home").mkdir(exist_ok=True)
    proj = tmp_path / "proj" / "blog"
    proj.mkdir(parents=True)
    (proj / "index.php").write_text("")
    c.park(tmp_path / "proj")
    c.ngrok.bin.write_text("")
    return c


def save(ctl, token="2abcDEF_1234567890"):
    f = ctl.ngrok.config_files[0]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f'version: "3"\nagent:\n    authtoken: {token}\n')


def test_states_missing_unverified_verified(ctl, monkeypatch):
    assert ctl.ngrok_state() == "missing"
    save(ctl)
    assert ctl.ngrok_state() == "unverified"
    monkeypatch.setattr(ctl.ngrok, "verify", lambda **k: TokenCheck(True, "ok"))
    assert ctl.verify_ngrok().ok and ctl.ngrok_state() == "verified"


def test_a_failed_check_leaves_it_unverified(ctl, monkeypatch):
    save(ctl)
    monkeypatch.setattr(ctl.ngrok, "verify", lambda **k: TokenCheck(False, "bad token (ERR_NGROK_105)"))
    res = ctl.verify_ngrok()
    assert not res.ok and ctl.ngrok_state() == "unverified"


def test_changing_the_token_requires_verifying_again(ctl, monkeypatch):
    save(ctl, "firstTOKEN_1234567")
    monkeypatch.setattr(ctl.ngrok, "verify", lambda **k: TokenCheck(True, "ok"))
    ctl.verify_ngrok()
    assert ctl.ngrok_state() == "verified"
    save(ctl, "secondTOKEN_123456")
    assert ctl.ngrok_state() == "unverified"      # the old verification does not carry over to another token


def test_verified_state_is_remembered_without_storing_the_token(ctl, monkeypatch, paths):
    save(ctl)
    monkeypatch.setattr(ctl.ngrok, "verify", lambda **k: TokenCheck(True, "ok"))
    ctl.verify_ngrok()
    assert "2abcDEF_1234567890" not in paths.config_file.read_text()
    again = Controller(paths, ctl.runner, home=ctl.home)
    assert again.ngrok_state() == "verified"


def test_share_requires_a_verified_token(ctl, runner, monkeypatch):
    with pytest.raises(ControllerError, match="authtoken"):
        ctl.share("blog")
    save(ctl)
    with pytest.raises(ControllerError, match="Verify your ngrok token"):
        ctl.share("blog")
    assert not runner.ran("systemctl", "--user", "restart", "pipanel-ngrok.service")
    monkeypatch.setattr(ctl.ngrok, "verify", lambda **k: TokenCheck(True, "ok"))
    ctl.verify_ngrok()
    monkeypatch.setattr(type(ctl.services), "failures", lambda self, names, **k: {})
    ctl.share("blog")
    assert runner.ran("systemctl", "--user", "restart", "pipanel-ngrok.service")


def test_cannot_verify_while_a_tunnel_is_running(ctl, runner):
    save(ctl)
    runner.on(["systemctl", "--user", "is-active"], Result(0, "active\n"))
    (ctl.paths.systemd_user / "pipanel-ngrok.service").write_text("")
    with pytest.raises(ControllerError, match="Stop sharing first"):
        ctl.verify_ngrok()
