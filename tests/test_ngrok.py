import pytest

from pipanel.core.controller import Controller, ControllerError
from pipanel.core.ngrok import Ngrok
from pipanel.core.runner import Result

from .conftest import pump
from .test_ui import page_of, win  # noqa: F401  (fixtures)


@pytest.fixture
def ng(paths, runner, tmp_path):
    return Ngrok(paths, runner, home=tmp_path / "home", config_home=tmp_path / "home" / ".config")


def write_cfg(ng, text, which=0):
    f = ng.config_files[which]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def test_no_config_file_means_no_token(ng):
    assert not ng.has_authtoken()


@pytest.mark.parametrize("text", [
    'version: "3"\nagent:\n    authtoken: 2abcDEF_1234567890\n',      # v3 format written by `config add-authtoken`
    "authtoken: 2abcDEF_1234567890\n",                                  # flat / older format
    'authtoken: "2abcDEF_1234567890"\n',
])
def test_saved_token_is_detected(ng, text):
    write_cfg(ng, text)
    assert ng.has_authtoken()


@pytest.mark.parametrize("text", ['version: "3"\nagent:\n    authtoken: ""\n', "authtoken:\n", "# authtoken: abc\n",
                                  'version: "3"\nagent:\n    log: stdout\n'])
def test_empty_or_commented_token_is_not_a_token(ng, text):
    write_cfg(ng, text)
    assert not ng.has_authtoken()


def test_older_config_location_is_also_read(ng):
    write_cfg(ng, "authtoken: 2abcDEF_1234567890\n", which=1)
    assert ng.has_authtoken()


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


def test_share_without_token_explains_and_changes_nothing(ctl, runner, paths):
    with pytest.raises(ControllerError, match="authtoken"):
        ctl.share("blog")
    assert not (paths.systemd_user / "pipanel-ngrok.service").exists()
    assert not runner.ran("systemctl", "--user", "restart", "pipanel-ngrok.service")


def verified(ctl):
    from pipanel.core.ngrok import fingerprint
    ctl.cfg.ngrok_verified = fingerprint(ctl.ngrok.read_token())


def test_share_with_token_starts_the_tunnel(ctl, runner, monkeypatch):
    write_cfg(ctl.ngrok, "authtoken: 2abcDEF_1234567890\n")
    verified(ctl)
    monkeypatch.setattr(type(ctl.services), "failures", lambda self, names, **k: {})
    ctl.share("blog")
    assert runner.ran("systemctl", "--user", "restart", "pipanel-ngrok.service")


def test_share_reports_why_ngrok_died_and_stops_the_restart_loop(ctl, runner, monkeypatch):
    write_cfg(ctl.ngrok, "authtoken: badtoken_1234567890\n")
    verified(ctl)
    err = "ERROR:  authentication failed: The authtoken you specified is invalid. ERR_NGROK_105"
    monkeypatch.setattr(type(ctl.services), "failures", lambda self, names, **k: {"ngrok": err})
    with pytest.raises(ControllerError, match="ERR_NGROK_105"):
        ctl.share("blog")
    assert runner.ran("systemctl", "--user", "stop", "pipanel-ngrok.service")


def test_share_page_blocks_start_until_the_token_is_verified(qapp, win, ui_ctl, runner, monkeypatch):    # noqa: F811
    from pipanel.core.ngrok import TokenCheck
    ui_ctl.ngrok.bin.write_text("")        # pretend ngrok is installed
    win.go("Share")
    page = page_of(win, "Share")
    pump(qapp, win, 8)
    # 1. no token
    assert not page.start.isEnabled() and "needs a free account" in page.auth_state.text()
    assert page.verify_btn.isHidden() and not page.get_token.itemAt(0).widget().isHidden()
    # 2. token saved but not verified: still blocked, and the Verify button appears
    write_cfg(ui_ctl.ngrok, 'version: "3"\nagent:\n    authtoken: 2abcDEF_1234567890\n')
    page.refresh()
    pump(qapp, win, 8)
    assert not page.start.isEnabled() and "not verified yet" in page.auth_state.text()
    assert not page.verify_btn.isHidden() and page.verify_btn.text() == "Verify token"
    # 3. ngrok rejects it: stays blocked and the reason is shown
    errors = []
    monkeypatch.setattr(win, "error", errors.append)
    monkeypatch.setattr(ui_ctl.ngrok, "verify", lambda **k: TokenCheck(False, "invalid (ERR_NGROK_105)"))
    page.verify_token()
    pump(qapp, win, 15)
    assert not page.start.isEnabled() and "ERR_NGROK_105" in errors[0]
    # 4. ngrok accepts it: Start is enabled
    monkeypatch.setattr(ui_ctl.ngrok, "verify", lambda **k: TokenCheck(True, "ok"))
    page.verify_token()
    pump(qapp, win, 15)
    assert page.start.isEnabled() and "Token verified" in page.auth_state.text()
    assert page.verify_btn.text() == "Verify again"
