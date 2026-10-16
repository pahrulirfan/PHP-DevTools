"""Run slow functions in a thread pool; callbacks always run on the UI thread.

Every labelled task is tracked (queued / running / done / failed) so the UI can show all of them. Tasks that
pass the same `lock` never run at the same time (for example everything that goes through apt, which holds a
global dpkg lock); everything else runs in parallel.
"""
from __future__ import annotations

import itertools
import time
import traceback
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from ..core.runner import Result

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
MAX_TRACKED = 8   # oldest finished tasks are dropped beyond this


@dataclass
class TaskInfo:
    id: int
    label: str
    lock: str | None = None
    state: str = RUNNING
    message: str = ""            # why it failed (shown on request)
    started: float = field(default_factory=time.monotonic)

    @property
    def active(self) -> bool:
        return self.state in (QUEUED, RUNNING)


def outcome(value: Any) -> tuple[bool, str]:
    """Did a task's return value succeed? Understands Result and lists of (step name, Result)."""
    if isinstance(value, Result):
        return value.ok, "" if value.ok else value.text
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], Result) and not item[1].ok:
                return False, f"{item[0]}: {item[1].text}".strip()
    return True, ""


class _Job(QRunnable):
    def __init__(self, jid: int, fn: Callable[[], Any], owner: "Tasks"):
        super().__init__()
        self.jid, self.fn, self.owner = jid, fn, owner

    def run(self) -> None:
        try:
            self.owner.finished.emit(self.jid, True, self.fn())
        except Exception as e:  # noqa: BLE001 - forwarded to the UI
            self.owner.finished.emit(self.jid, False, e)
            traceback.print_exc()


class Tasks(QObject):
    finished = Signal(int, bool, object)
    changed = Signal()   # a tracked task was added, started, finished, or dismissed

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._ids = itertools.count(1)
        self._jobs: dict[int, tuple[Callable[[], Any], Callable | None, Callable | None]] = {}
        self._queued: dict[str, deque[int]] = defaultdict(deque)   # lock -> waiting task ids
        self._holder: dict[str, int] = {}                          # lock -> running task id
        self.infos: dict[int, TaskInfo] = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(6)
        self.finished.connect(self._on_finished)   # a slot owned by this QObject -> runs on the UI thread
        self.error_handler: Callable[[Exception], None] | None = None

    # ---- public ----
    def run(self, fn: Callable[[], Any], done: Callable[[Any], None] | None = None,
            failed: Callable[[Exception], None] | None = None, label: str = "", lock: str | None = None) -> int:
        jid = next(self._ids)
        self._jobs[jid] = (fn, done, failed)
        if label:
            self.infos[jid] = TaskInfo(jid, label, lock, QUEUED if lock in self._holder else RUNNING)
            self._trim()
        if lock and lock in self._holder:
            self._queued[lock].append(jid)
        else:
            self._start(jid, lock)
        self.changed.emit()
        return jid

    def active(self) -> list[TaskInfo]:
        return [t for t in self.infos.values() if t.active]

    def dismiss(self, jid: int) -> None:
        info = self.infos.get(jid)
        if info and not info.active:
            del self.infos[jid]
            self.changed.emit()

    def wait(self, ms: int = 5000) -> bool:
        return self._pool.waitForDone(ms)

    # ---- internals ----
    def _start(self, jid: int, lock: str | None) -> None:
        if lock:
            self._holder[lock] = jid
        if jid in self.infos:
            self.infos[jid].state = RUNNING
        self._pool.start(_Job(jid, self._jobs[jid][0], self))

    def _trim(self) -> None:
        finished = [i for i, t in self.infos.items() if not t.active]
        for jid in finished[:max(0, len(finished) - (MAX_TRACKED - len(self.active())))]:
            del self.infos[jid]

    @Slot(int, bool, object)
    def _on_finished(self, jid: int, ok: bool, value: Any) -> None:
        _fn, done, failed = self._jobs.pop(jid)
        info = self.infos.get(jid)
        if info:
            good, msg = outcome(value) if ok else (False, str(value))
            info.state, info.message = (DONE if good else FAILED), msg
            lock = info.lock
        else:
            lock = next((k for k, v in self._holder.items() if v == jid), None)
        if lock and self._holder.get(lock) == jid:
            del self._holder[lock]
            if self._queued[lock]:
                self._start(self._queued[lock].popleft(), lock)
        self._trim()
        self.changed.emit()
        if ok:
            if done:
                done(value)
        elif failed:
            failed(value)
        elif self.error_handler:
            self.error_handler(value)
