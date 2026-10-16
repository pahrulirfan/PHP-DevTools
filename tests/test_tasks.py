import threading

import pytest

from pipanel.core.runner import Result
from pipanel.ui.activity import ActivityPanel
from pipanel.ui.tasks import DONE, FAILED, QUEUED, RUNNING, Tasks, outcome


def spin(qapp, tasks, until, rounds=200):
    for _ in range(rounds):
        tasks.wait(20)
        qapp.processEvents()
        if until():
            return True
    return False


@pytest.fixture
def tasks(qapp):
    t = Tasks()
    yield t
    t.wait(2000)


# ---------- outcome ----------
def test_outcome_understands_results_and_steps():
    assert outcome(Result(0)) == (True, "")
    assert outcome(Result(1, "", "boom")) == (False, "boom")
    assert outcome([("a", Result(0)), ("b", Result(2, "", "bad"))]) == (False, "b: bad")
    assert outcome([("a", Result(0))]) == (True, "")
    assert outcome("anything else") == (True, "") and outcome(None) == (True, "")


# ---------- parallel vs serial ----------
def test_independent_tasks_run_at_the_same_time_and_are_all_listed(qapp, tasks):
    gate = threading.Event()
    started = threading.Barrier(2, timeout=5)

    def work():
        started.wait()          # both tasks must be running at once for this to pass
        gate.wait(5)
    tasks.run(work, label="Installing Node 22…", lock="node")
    tasks.run(work, label="Installing MariaDB…", lock="system")
    assert spin(qapp, tasks, lambda: all(t.state == RUNNING for t in tasks.infos.values()) and len(tasks.infos) == 2)
    assert {t.label for t in tasks.active()} == {"Installing Node 22…", "Installing MariaDB…"}
    gate.set()
    assert spin(qapp, tasks, lambda: not tasks.active())
    assert [t.state for t in tasks.infos.values()] == [DONE, DONE]   # both results stay visible


def test_same_lock_runs_one_after_another_in_order(qapp, tasks):
    order, gate = [], threading.Event()
    tasks.run(lambda: (order.append("a-start"), gate.wait(5), order.append("a-end")), label="A", lock="system")
    tasks.run(lambda: order.append("b"), label="B", lock="system")
    tasks.run(lambda: order.append("c"), label="C", lock="system")
    assert spin(qapp, tasks, lambda: "a-start" in order)
    states = [t.state for t in tasks.infos.values()]
    assert states == [RUNNING, QUEUED, QUEUED] and "b" not in order    # B and C wait for A
    gate.set()
    assert spin(qapp, tasks, lambda: not tasks.active())
    assert order == ["a-start", "a-end", "b", "c"]


def test_queue_continues_after_a_failure(qapp, tasks):
    ran = []
    tasks.run(lambda: 1 / 0, label="first", lock="system", failed=lambda e: None)
    tasks.run(lambda: ran.append(1), label="second", lock="system")
    assert spin(qapp, tasks, lambda: not tasks.active())
    assert ran == [1]
    assert [t.state for t in tasks.infos.values()] == [FAILED, DONE]


# ---------- result tracking ----------
def test_failed_result_is_marked_failed_even_without_exception(qapp, tasks):
    tasks.run(lambda: [("Install MariaDB", Result(100, "", "E: Unable to locate package"))], label="Installing…")
    assert spin(qapp, tasks, lambda: not tasks.active())
    info = next(iter(tasks.infos.values()))
    assert info.state == FAILED and "Unable to locate package" in info.message


def test_exception_message_kept_and_dismiss(qapp, tasks):
    tasks.run(lambda: 1 / 0, label="boom", failed=lambda e: None)
    assert spin(qapp, tasks, lambda: not tasks.active())
    jid = next(iter(tasks.infos))
    assert tasks.infos[jid].state == FAILED and "division" in tasks.infos[jid].message
    tasks.dismiss(jid)
    assert tasks.infos == {}


def test_running_task_cannot_be_dismissed(qapp, tasks):
    gate = threading.Event()
    jid = tasks.run(lambda: gate.wait(5), label="slow")
    tasks.dismiss(jid)
    assert jid in tasks.infos
    gate.set()


def test_only_the_newest_finished_tasks_are_kept(qapp, tasks):
    for i in range(12):
        tasks.run(lambda: None, label=f"t{i}")
    assert spin(qapp, tasks, lambda: not tasks.active())
    assert len(tasks.infos) <= 8 and "t11" in {t.label for t in tasks.infos.values()}


# ---------- the panel the user sees ----------
def test_panel_shows_every_task_and_keeps_failures(qapp, tasks):
    panel = ActivityPanel(tasks, show_details=lambda text: None)
    gate = threading.Event()
    tasks.run(lambda: gate.wait(5), label="Installing Node 22…", lock="node")
    tasks.run(lambda: gate.wait(5), label="Installing MariaDB…", lock="system")
    tasks.run(lambda: [("apt", Result(1, "", "locked"))], label="Installing PHP 8.4…", lock="system")
    assert spin(qapp, tasks, lambda: [t.state for t in tasks.infos.values()] == [RUNNING, RUNNING, QUEUED])
    texts = " | ".join(l.text() for l in panel.findChildren(type(panel.rows.parent().findChild(object))) if False) or ""
    from PySide6.QtWidgets import QLabel
    shown = [l.text() for l in panel.findChildren(QLabel)]
    assert "Installing Node 22" in shown and "Installing MariaDB" in shown and "Installing PHP 8.4" in shown
    assert any("Waiting" in t for t in shown)               # the queued one explains why it is not running
    assert not panel.isHidden()
    gate.set()
    assert spin(qapp, tasks, lambda: not tasks.active())
    shown = [l.text() for l in panel.findChildren(QLabel)]
    assert shown.count("Done") == 2 and shown.count("Failed") == 1     # nothing disappears silently
    from PySide6.QtWidgets import QPushButton
    assert "Details" in [b.text() for b in panel.findChildren(QPushButton)]


def test_panel_auto_dismisses_successes_but_not_failures(qapp, tasks):
    panel = ActivityPanel(tasks, show_details=lambda text: None)
    tasks.run(lambda: None, label="ok")
    tasks.run(lambda: [("x", Result(1, "", "bad"))], label="bad")
    assert spin(qapp, tasks, lambda: not tasks.active())
    ok_id = next(i for i, t in tasks.infos.items() if t.label == "ok")
    panel._auto_dismiss(ok_id)
    assert [t.label for t in tasks.infos.values()] == ["bad"]
    panel._auto_dismiss(next(iter(tasks.infos)))
    assert [t.label for t in tasks.infos.values()] == ["bad"]        # failed stays until dismissed
