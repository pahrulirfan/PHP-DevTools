"""A table for lists that can grow to hundreds of rows: filter, sort by any column, and pages, so only one page of
rows (and their action buttons) exists at a time."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .widgets import button, label

ROW_HEIGHT = 40
PAGE_SIZE = 20


@dataclass
class Column:
    title: str
    numeric: bool = False     # right-aligned
    stretch: bool = False


@dataclass
class Row:
    key: str                  # what the actions and the selection refer to (a database name, user@host, ...)
    cells: list[str]          # shown text
    sort: list[Any] = field(default_factory=list)   # sort value per column (defaults to the text)
    search: str = ""          # extra text the filter matches (defaults to the cells)

    def sort_value(self, col: int) -> Any:
        return self.sort[col] if col < len(self.sort) else self.cells[col].lower()

    def matches(self, words: list[str]) -> bool:
        hay = (" ".join(self.cells) + " " + self.search).lower()
        return all(w in hay for w in words)


class Pager(QWidget):
    """«‹  21–40 of 124  ›». Hidden when everything fits on one page."""
    changed = Signal()

    def __init__(self, size: int = PAGE_SIZE):
        super().__init__()
        self.size, self.page, self.total = size, 0, 0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.info = label("", "muted")
        self.prev = button("‹ Previous", None, on_click=lambda: self.go(self.page - 1))
        self.next = button("Next ›", None, on_click=lambda: self.go(self.page + 1))
        lay.addStretch(1)
        for w in (self.info, self.prev, self.next):
            lay.addWidget(w)

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.size))

    def set_total(self, total: int, reset: bool = False) -> None:
        self.total = total
        self.page = 0 if reset else min(self.page, self.pages - 1)
        self._update()

    def go(self, page: int) -> None:
        page = max(0, min(self.pages - 1, page))
        if page != self.page:
            self.page = page
            self._update()
            self.changed.emit()

    def window(self, items: list) -> list:
        return items[self.page * self.size:(self.page + 1) * self.size]

    def _update(self) -> None:
        first = self.page * self.size
        self.info.setText(f"{first + 1}–{min(first + self.size, self.total)} of {self.total}")
        self.prev.setEnabled(self.page > 0)
        self.next.setEnabled(self.page < self.pages - 1)
        self.setVisible(self.total > self.size)


class DataTable(QWidget):
    """Rows in, one sorted and filtered page out. `actions(row)` builds the widget of the last column."""
    activated = Signal(str)   # double-clicked row key

    def __init__(self, columns: list[Column], actions: Callable[[Row], QWidget] | None = None,
                 actions_width: int = 0, empty: str = "Nothing here yet.", page_size: int = PAGE_SIZE):
        super().__init__()
        self.columns, self.actions = columns, actions
        self.rows: list[Row] = []
        self.words: list[str] = []
        self.sort_col, self.sort_order = 0, Qt.AscendingOrder
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        n = len(columns) + (1 if actions else 0)
        self.table = QTableWidget(0, n)
        self.table.setHorizontalHeaderLabels([c.title for c in columns] + ([""] if actions else []))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # a page always fits; the page scrolls
        head = self.table.horizontalHeader()
        head.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        head.setHighlightSections(False)
        head.setSectionsClickable(True)
        head.setSortIndicatorShown(True)
        head.setSortIndicator(0, Qt.AscendingOrder)
        head.sectionClicked.connect(self._sort_clicked)
        for i, c in enumerate(columns):
            head.setSectionResizeMode(i, QHeaderView.Stretch if c.stretch else QHeaderView.ResizeToContents)
        if actions:
            head.setSectionResizeMode(n - 1, QHeaderView.Fixed)
            head.resizeSection(n - 1, actions_width)
        self.table.doubleClicked.connect(lambda i: self._activated(i.row()))
        self.empty_text = empty
        self.empty = label(empty, "muted")
        self.pager = Pager(page_size)
        self.pager.changed.connect(self.render)
        lay.addWidget(self.table)
        lay.addWidget(self.empty)
        lay.addWidget(self.pager)
        self._shown: list[Row] = []

    # ---- data ----
    def set_rows(self, rows: list[Row]) -> None:
        keep = self.selected()
        self.rows = rows
        self.render()
        if keep:
            self.select(keep)

    def set_filter(self, text: str) -> None:
        self.words = text.lower().split()
        self.pager.set_total(len(self.visible()), reset=True)
        self.render()

    def visible(self) -> list[Row]:
        rows = [r for r in self.rows if r.matches(self.words)]
        return sorted(rows, key=lambda r: r.sort_value(self.sort_col), reverse=self.sort_order == Qt.DescendingOrder)

    def _sort_clicked(self, col: int) -> None:
        if col >= len(self.columns):
            self.table.horizontalHeader().setSortIndicator(self.sort_col, self.sort_order)
            return
        if col == self.sort_col:
            self.sort_order = Qt.DescendingOrder if self.sort_order == Qt.AscendingOrder else Qt.AscendingOrder
        else:
            self.sort_col, self.sort_order = col, Qt.AscendingOrder
        self.table.horizontalHeader().setSortIndicator(self.sort_col, self.sort_order)
        self.render()

    # ---- drawing: only the current page ----
    def render(self) -> None:
        rows = self.visible()
        self.pager.set_total(len(rows))
        self._shown = self.pager.window(rows)
        self.table.setRowCount(0)
        for i, row in enumerate(self._shown):
            self.table.insertRow(i)
            for c, col in enumerate(self.columns):
                item = QTableWidgetItem(row.cells[c])
                item.setToolTip(row.cells[c])
                if col.numeric:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, item)
            if self.actions:
                self.table.setCellWidget(i, len(self.columns), self.actions(row))
        self.table.setVisible(bool(self._shown))
        self.empty.setVisible(not self._shown)
        self.empty.setText("Nothing matches the filter." if self.words and self.rows else self.empty_text)
        self.table.setFixedHeight(self.table.horizontalHeader().sizeHint().height() + len(self._shown) * ROW_HEIGHT + 4)

    def keys_shown(self) -> list[str]:
        return [r.key for r in self._shown]

    def selected(self) -> str | None:
        i = self.table.currentRow()
        return self._shown[i].key if 0 <= i < len(self._shown) else None

    def select(self, key: str) -> None:
        """Select a row, turning to its page."""
        rows = self.visible()
        idx = next((i for i, r in enumerate(rows) if r.key == key), None)
        if idx is None:
            return
        self.pager.go(idx // self.pager.size)
        self.render()
        self.table.selectRow(idx % self.pager.size)

    def _activated(self, i: int) -> None:
        if 0 <= i < len(self._shown):
            self.activated.emit(self._shown[i].key)


def row_actions(buttons: list[QWidget]) -> QWidget:
    """The action buttons of one row, on a transparent background."""
    w = QWidget()
    w.setObjectName("rowActions")
    w.setStyleSheet("#rowActions { background: transparent; }")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(4, 0, 8, 0)
    lay.setSpacing(4)
    for b in buttons:
        b.setMinimumWidth(b.sizeHint().width())
        lay.addWidget(b)
    lay.addStretch(1)
    return w
