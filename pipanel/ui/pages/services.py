from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout

from ...core.doctor import OK, WARN
from ...core.services import service_title
from ..widgets import _gone, Card, StatusDot, Toggle, button, clear_layout, hbox, label
from .base import Page

STATE_TEXT = {"active": "Running", "inactive": "Stopped", "failed": "Error", "not-found": "Not created",
              "activating": "Starting…", "unknown": "?"}
STATE_DOT = {"active": "on", "failed": "error"}


service_label = service_title   # kept for callers that import it from here


class ServicesPage(Page):
    title = "Services"
    icon_name = "services"

    def build(self) -> None:
        self.cards = Card("Service")
        self.rows: dict[str, tuple[StatusDot, object, Toggle]] = {}
        self.cards_lay = self.cards.body
        self.body.addWidget(self.cards)

        self.doc = Card("Doctor")
        self.doc_lay = self.doc.body
        self.doc_btn = button("Run diagnostics", "primary", "stethoscope", self.run_doctor)
        self.doc_lay.addLayout(hbox(label("Checks ports, DNS, certificates, and conflicting services."), None, self.doc_btn))
        self.doc_results = QHBoxLayout()
        self.results_box = Card()
        self.results_box.setVisible(False)
        self.body.addWidget(self.doc)
        self.body.addWidget(self.results_box)
        self.win.monitor.changed.connect(self._states)

    def header_actions(self):
        return [button("Start all", "primary", "play", self.start_all),
                button("Stop all", None, "stop", self.stop_all)]

    def refresh(self) -> None:
        if self.win.monitor.states:
            self._states(self.win.monitor.states)
        self.poll()

    def poll(self) -> None:
        self.win.monitor.refresh()

    def _states(self, states: dict[str, str]) -> None:
        in_use = self.ctl.php_in_use()
        if set(states) != set(self.rows):
            clear_layout_after_title(self.cards_lay)
            self.rows.clear()
            for name in states:
                dot, text, tg = StatusDot(), label("", "muted"), Toggle()
                tg.setAccessibleName(service_label(name, self.ctl.db_label))
                tg.clicked.connect(lambda on, n=name: self.toggle(n, on))
                self.cards_lay.addLayout(hbox(dot, label(service_label(name, self.ctl.db_label), name="cardTitle"), text, None, tg,
                                              spacing=10))
                self.rows[name] = (dot, text, tg)
            if not states:
                self.cards_lay.addWidget(label("No services yet. Run the first-time setup.", "muted"))
        for name, st in states.items():
            dot, text, tg = self.rows[name]
            dot.set_state(STATE_DOT.get(st, "off"))
            idle = name.startswith("php") and name[3:] not in in_use and st == "inactive"
            text.setText("Not used by any site" if idle else STATE_TEXT.get(st, st))
            tg.blockSignals(True)
            tg.setChecked(st in ("active", "activating"))
            tg.blockSignals(False)

    def toggle(self, name: str, on: bool) -> None:
        fn = self.ctl.start_service if on else self.ctl.stop_service
        self.run(lambda: fn(name), done=lambda r: (self.win.error(r.text) if not r.ok else None, self.poll()))

    def start_all(self) -> None:
        self.run(self.ctl.start_all, label="Starting services…",
                 done=lambda r: (self.win.error(r.text) if not r.ok else None, self.poll()))

    def stop_all(self) -> None:
        self.run(self.ctl.stop_all, label="Stopping services…", done=lambda _r: self.poll())

    # ---- doctor ----
    def run_doctor(self) -> None:
        self.run(self.ctl.doctor, done=self._doctor, label="Checking…")

    def _doctor(self, checks) -> None:
        box = self.results_box
        clear_layout(box.body)
        box.setVisible(True)
        for c in checks:
            dot = StatusDot({"ok": "on", "warn": "warn", "fail": "error"}[c.status])
            texts = QHBoxLayout()
            texts.addWidget(label(c.title))
            if c.detail:
                texts.addWidget(label(c.detail, "muted"))
            texts.addStretch(1)
            row = hbox(dot, spacing=10)
            row.addLayout(texts, 1)
            if c.fix and c.status != OK:
                row.addWidget(button("Fix", None, on_click=lambda f=c.fix: self.fix(f)))
            box.body.addLayout(row)

    def fix(self, fix_id: str) -> None:
        def done(r):
            self.win.toast("Done") if r.ok else self.win.error(r.text)
            self.run_doctor()
        self.run(lambda: self.ctl.fix(fix_id), done=done, label="Fixing…", lock="system")


def clear_layout_after_title(lay) -> None:
    while lay.count() > 1:
        item = lay.takeAt(1)
        if item.widget():
            _gone(item.widget())
        elif item.layout():
            clear_layout(item.layout())
