"""Ctrl+K: find a site or an action by typing a few letters. Scales to hundreds of sites, unlike menus."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from ..core import actions
from ..core.sites import LARAVEL

MAX_RESULTS = 60


@dataclass
class Entry:
    title: str
    detail: str
    run: Callable[[], None]
    rank: int = 2   # lower first when the query is empty or matches equally well

    @property
    def text(self) -> str:
        return f"{self.title}   ·   {self.detail}" if self.detail else self.title


def matches(entries: list[Entry], query: str, limit: int = MAX_RESULTS) -> list[Entry]:
    """Every word of the query must appear; names that start with the query come first."""
    words = query.lower().split()
    if not words:
        return sorted(entries, key=lambda e: e.rank)[:limit]
    hits = [e for e in entries if all(w in f"{e.title} {e.detail}".lower() for w in words)]
    return sorted(hits, key=lambda e: (not e.title.lower().startswith(words[0]), e.rank, e.title))[:limit]


def build_entries(win) -> list[Entry]:
    ctl = win.ctl
    out: list[Entry] = []
    recent = {s.name for s in ctl.recent_sites()}

    def opener(name: str, fn: Callable[[], object]) -> Callable[[], None]:
        def run() -> None:
            ctl.opened(name)
            fn()
        return run

    for s in ctl.sites():
        rank = 0 if s.name in recent else 3
        host = f"{s.name}.{ctl.cfg.tld}"
        out += [Entry(host, "Open in browser", opener(s.name, lambda s=s: actions.open_url(ctl.site_url(s))), rank),
                Entry(host, "Open in VS Code", opener(s.name, lambda s=s: actions.open_vscode(s.path)), rank + 1),
                Entry(host, "Open terminal", opener(s.name, lambda s=s: actions.open_terminal(s.path)), rank + 1),
                Entry(host, "Open folder", opener(s.name, lambda s=s: actions.open_folder(s.path)), rank + 1)]
        if s.kind == LARAVEL:
            out.append(Entry(host, "Laravel page (packages, processes)",
                             lambda s=s: win.show_laravel(s.name), rank + 2))
    for x in ctl.proxies():
        out.append(Entry(f"{x.name}.{ctl.cfg.tld}", f"Open in browser (proxy to {x.target})",
                         opener(x.name, lambda x=x: actions.open_url(ctl.site_url(x))), 3))
    for page in win.pages:
        out.append(Entry(f"Go to {page.title}", "", lambda t=page.title: win.go(t), 4))
    for title, fn in (("Start all services", lambda: win.tasks.run(ctl.start_all, label="Starting services…")),
                      ("Stop all services", lambda: win.tasks.run(ctl.stop_all, label="Stopping services…")),
                      ("Run diagnostics", win.run_diagnostics),
                      ("New Laravel project", lambda: win.page("Sites").new_laravel()),
                      ("New WordPress site", lambda: win.page("Sites").new_wordpress()),
                      ("Add a proxy to a local port", lambda: win.page("Sites").add_proxy())):
        out.append(Entry(title, "", fn, 5))
    return out


class Palette(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Search")
        self.setModal(True)
        self.resize(620, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search sites and actions…   (Enter opens, Esc closes)")
        self.query.setAccessibleName("Search")
        self.list = QListWidget()
        self.list.setAccessibleName("Results")
        lay.addWidget(self.query)
        lay.addWidget(self.list)
        self.entries = build_entries(win)
        self.query.textChanged.connect(self.filter)
        self.query.returnPressed.connect(self.run_current)
        self.list.itemActivated.connect(lambda _i: self.run_current())
        self.query.installEventFilter(self)
        self.filter("")

    def filter(self, text: str) -> None:
        self.list.clear()
        for e in matches(self.entries, text):
            item = QListWidgetItem(e.text)
            item.setData(Qt.UserRole, e)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, ev) -> bool:   # arrow keys move through the results while typing
        if obj is self.query and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Down, Qt.Key_Up):
            row = self.list.currentRow() + (1 if ev.key() == Qt.Key_Down else -1)
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
            return True
        return super().eventFilter(obj, ev)

    def run_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        entry: Entry = item.data(Qt.UserRole)
        self.accept()
        entry.run()
