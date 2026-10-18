"""The agent that runs the services on Windows and macOS, exercised with real processes (on any system)."""
import sys
import threading
import time

import pytest

from pipanel.core import agent as A
from pipanel.core.supervisor import AgentSupervisor

PY = sys.executable
pytestmark = pytest.mark.native   # the real process handling of this machine (POSIX or Windows)


@pytest.fixture
def running(paths, monkeypatch):
    monkeypatch.setattr(A, "RESTART_DELAY", 0.05)
    ag = A.Agent(paths, poll=0.02)
    th = threading.Thread(target=ag.run, daemon=True)
    th.start()
    sup = AgentSupervisor(paths, start_agent=False)
    sup.SETTLE = 5
    for _ in range(100):
        if sup.agent_alive():
            break
        time.sleep(0.02)
    yield ag, sup
    for u in list(ag.units.values()):
        ag.stop(u)
    ag.running = False
    th.join(5)


def wait(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.03)
    return cond()


def sleeper(text="hello"):
    return dict(description="sleeper", argv=[PY, "-c", f"print({text!r}, flush=True); import time; time.sleep(60)"])


def test_start_stop_status_and_log(running, paths):
    ag, sup = running
    assert sup.status(["web"]) == {"web": "not-found"}
    sup.write("web", sleeper("hello from web"))
    assert sup.status(["web"]) == {"web": "inactive"}
    assert sup.ctl("start", "web").ok
    assert sup.status(["web"]) == {"web": "active"}
    pid = ag.units["web"].pid
    assert A.alive(pid)
    assert wait(lambda: "hello from web" in sup.journal("web"))
    assert sup.ctl("restart", "web").ok and wait(lambda: ag.units["web"].pid not in (0, pid))
    assert sup.ctl("stop", "web").ok
    assert sup.status(["web"]) == {"web": "inactive"} and not A.alive(pid)


def test_a_crashing_service_is_restarted_then_marked_failed(running):
    ag, sup = running
    sup.write("bad", dict(description="bad", argv=[PY, "-c", "import sys; print('boom'); sys.exit(3)"]))
    sup.ctl("start", "bad")
    assert wait(lambda: sup.status(["bad"])["bad"] == "failed")
    assert len(ag.units["bad"].failures) >= A.BURST
    assert sup.journal("bad").count("boom") >= A.BURST
    sup.write("bad", sleeper())            # fixed: a new start begins with a clean slate
    sup.ctl("start", "bad")
    assert wait(lambda: sup.status(["bad"])["bad"] == "active")


def test_reload_runs_the_reload_command(running, tmp_path):
    ag, sup = running
    marker = tmp_path / "reloaded"
    sup.write("web", {**sleeper(), "reload": [PY, "-c", f"open({str(marker)!r}, 'w').write('x')"]})
    sup.ctl("start", "web")
    sup.ctl("reload", "web")
    assert wait(marker.exists)


def test_missing_program_fails_cleanly(running):
    ag, sup = running
    sup.write("ghost", dict(description="ghost", argv=["/nonexistent/program"]))
    sup.ctl("start", "ghost")
    assert wait(lambda: sup.status(["ghost"])["ghost"] == "failed")
    assert "cannot start" in sup.journal("ghost")


def test_enabled_services_start_with_the_agent_and_are_adopted(paths, monkeypatch):
    sup = AgentSupervisor(paths, start_agent=False)
    sup.write("db", sleeper())
    sup.ctl("enable", "db")
    first = A.Agent(paths, poll=0.02)
    first.lock()
    first.load()
    first.start_enabled()
    first.write_status()
    pid = first.units["db"].pid
    assert A.alive(pid)
    # the agent dies; a new one takes the running service over instead of starting it twice
    second = A.Agent(paths, poll=0.02)
    second.load()
    second.adopt()
    second.start_enabled()
    assert second.units["db"].pid == pid and second.units["db"].state == "active"
    second.stop(second.units["db"])
    first.units["db"].proc.wait(5)      # in real life the old agent is gone and the system reaps it
    assert not A.alive(pid)
    sup.ctl("disable", "db")
    assert not (sup.units / "db.enabled").exists()


def test_only_one_agent(paths):
    import subprocess
    other = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])
    try:
        a = A.Agent(paths)
        a.control.mkdir(parents=True, exist_ok=True)
        (a.control / "agent.pid").write_text(str(other.pid))   # another live agent holds the lock
        assert not a.lock()
    finally:
        other.kill()
        other.wait()
    assert a.lock()                                            # its pid is gone: the lock is free


def test_watch_runs_the_sync_command_when_a_folder_appears(running, tmp_path):
    ag, sup = running
    projects = tmp_path / "projects"
    projects.mkdir()
    marker = tmp_path / "synced"
    sup.set_watch([str(projects)], [PY, "-c", f"open({str(marker)!r}, 'a').write('x')"], True)
    time.sleep(0.3)
    assert not marker.exists()          # the first look only records what is there
    (projects / "blog").mkdir()
    assert wait(marker.exists)
    sup.set_watch([], [], True)
    assert not (sup.units / "watch.folders").exists()


def test_status_without_an_agent_is_inactive(paths):
    sup = AgentSupervisor(paths, start_agent=False)
    sup.write("web", sleeper())
    assert sup.status(["web"]) == {"web": "inactive"}
    r = sup.ctl("start", "web")
    assert not r.ok and "agent" in r.text
    assert not sup.ctl("start", "nope").ok
