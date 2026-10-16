"""Small widgets: toggle, status dot, badge, card, icon button."""
from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QPointF, QRectF, QSize, Qt, Property
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme
from .icons import icon


def _qc(key: str) -> QColor:
    return QColor(theme.tokens()[key])


class Toggle(QAbstractButton):
    """Custom on/off switch. Focusable and toggled with Space."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self, duration=120, easingCurve=QEasingCurve.InOutQuad)
        self.setCheckable(True)
        super().setChecked(checked)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 22)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool) -> None:   # changes made from code: immediate, no animation
        super().setChecked(on)
        self._anim.stop()
        self._pos = 1.0 if on else 0.0
        self.update()

    def _get(self) -> float: return self._pos
    def _set(self, v: float) -> None:
        self._pos = v
        self.update()
    knob = Property(float, _get, _set)

    def sizeHint(self) -> QSize: return QSize(40, 22)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = _qc("primary")
        off = _qc("off")
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos))
        if not self.isEnabled():
            track.setAlphaF(0.45)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 1, 40, 20), 10, 10)
        p.setBrush(QColor("#FFFFFF"))
        x = 3 + self._pos * 18
        p.drawEllipse(QPointF(x + 8, 11), 8, 8)
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            p.setPen(_qc("primary-text"))
            p.drawRoundedRect(QRectF(0.5, 0.5, 39, 21), 11, 11)


STATE_COLORS = {"on": "success", "off": "off", "error": "danger", "warn": "warning"}


class StatusDot(QWidget):
    def __init__(self, state: str = "off", parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self.setFixedSize(12, 12)

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(_qc(STATE_COLORS.get(self.state, "off")))
        p.drawEllipse(QRectF(1, 1, 10, 10))


def badge(text: str, kind: str = "muted") -> QLabel:
    lb = QLabel(text)
    lb.setProperty("badge", kind)
    lb.setAlignment(Qt.AlignCenter)
    lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return lb


def label(text: str = "", role: str | None = None, name: str | None = None, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse) if role in ("mono",) else None
    return lb


class Card(QFrame):
    def __init__(self, title: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 16, 20, 16)
        self.body.setSpacing(10)
        if title:
            self.body.addWidget(label(title, name="cardTitle"))


def button(text: str, variant: str | None = None, icon_name: str | None = None,
           on_click=None, tooltip: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    b.setCursor(Qt.PointingHandCursor)
    if tooltip:
        b.setToolTip(tooltip)
    if icon_name:
        _set_icon(b, icon_name, variant)
        theme.bus.changed.connect(lambda _t, b=b, n=icon_name, v=variant: _set_icon(b, n, v))
    if on_click:
        b.clicked.connect(lambda _=False: on_click())
    return b


def _set_icon(b: QPushButton, name: str, variant: str | None) -> None:
    try:
        color = theme.tokens()["on-primary"] if variant in ("primary", "danger") else theme.tokens()["text-muted"]
        b.setIcon(icon(name, color, 16))
        b.setIconSize(QSize(16, 16))
    except RuntimeError:   # the widget was already deleted
        pass


def icon_button(name: str, tooltip: str, on_click=None) -> QPushButton:
    b = button("", "ghost", name, on_click, tooltip)
    b.setAccessibleName(tooltip)
    b.setFixedSize(30, 30)
    return b


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0), stretch_after: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        else:
            lay.addWidget(w)
    return lay


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w:
            w.hide()
            w.setParent(None)   # detach now; deleteLater alone leaves the old image painted
            w.deleteLater()
        elif item.layout():
            clear_layout(item.layout())


class EmptyState(QWidget):
    def __init__(self, icon_name: str, text: str, action: QPushButton | None = None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(12)
        pic = QLabel()
        pic.setAlignment(Qt.AlignCenter)
        self._pic, self._name = pic, icon_name
        self._recolor()
        theme.bus.changed.connect(lambda _t: self._recolor())
        msg = label(text, "muted", wrap=True)
        msg.setAlignment(Qt.AlignCenter)
        lay.addWidget(pic)
        lay.addWidget(msg)
        if action:
            lay.addWidget(action, 0, Qt.AlignCenter)

    def _recolor(self) -> None:
        try:
            self._pic.setPixmap(icon(self._name, theme.tokens()["primary-text"], 48).pixmap(48, 48))
        except RuntimeError:
            pass


def _gone(w) -> None:
    w.hide()
    w.setParent(None)
    w.deleteLater()


class Banner(QWidget):
    """A highlighted one-line notice with an action button."""

    def __init__(self, text: str, action_text: str, on_click, icon_name: str | None = None):
        super().__init__()
        self.setObjectName("banner")
        self.setAttribute(Qt.WA_StyledBackground, True)   # a plain QWidget ignores QSS backgrounds otherwise
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        self.text = label(text, wrap=True)
        lay.addWidget(self.text, 1)
        self.action = button(action_text, "primary", icon_name, on_click)
        lay.addWidget(self.action)
