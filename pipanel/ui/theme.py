"""Light/dark color tokens, QSS rendering, QPalette, system theme detection."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

LIGHT = {
    "bg": "#FAF9FC", "surface": "#FFFFFF", "surface-2": "#F3F0FA", "border": "#E7E3F0",
    "text": "#1F1B2E", "text-muted": "#6B6580",
    "primary": "#7C3AED", "primary-hover": "#6D28D9", "primary-text": "#7C3AED",
    "primary-soft": "#EDE9FE", "on-primary": "#FFFFFF",
    "accent": "#14B8A6",
    "success": "#14B8A6", "success-text": "#0F766E",
    "warning": "#F59E0B", "warning-text": "#B45309",
    "danger": "#F43F5E", "danger-text": "#BE123C", "danger-button": "#BE123C",
    "info": "#6366F1", "info-text": "#4F46E5",
    "off": "#B8B2C8",
}
DARK = {
    "bg": "#14111F", "surface": "#1C1830", "surface-2": "#251F3D", "border": "#2E2848",
    "text": "#EDEAF7", "text-muted": "#9A93B5",
    "primary": "#7C3AED", "primary-hover": "#8B5CF6", "primary-text": "#A78BFA",
    "primary-soft": "#2A2250", "on-primary": "#FFFFFF",
    "accent": "#2DD4BF",
    "success": "#2DD4BF", "success-text": "#2DD4BF",
    "warning": "#FBBF24", "warning-text": "#FBBF24",
    "danger": "#FB7185", "danger-text": "#FB7185", "danger-button": "#BE123C",
    "info": "#818CF8", "info-text": "#818CF8",
    "off": "#5B5478",
}
TOKENS = {"light": LIGHT, "dark": DARK}
TEMPLATE = Path(__file__).with_name("style.qss.tmpl")


class _Bus(QObject):
    changed = Signal(str)   # 'light' | 'dark'


bus = _Bus()
_current = "light"


def tokens() -> dict[str, str]:
    """Tokens of the active theme (used by widgets that paint themselves)."""
    return TOKENS[_current]


# ---------- contrast (used by tests) ----------
def _lum(hex_: str) -> float:
    h = hex_.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ---------- QSS ----------
def render_qss(tokens: dict[str, str], extra: dict[str, str] | None = None) -> str:
    values = {**tokens, **(extra or {})}
    text = TEMPLATE.read_text()

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"unknown QSS token: {key}")
        return values[key]

    return re.sub(r"\{\{([a-z0-9_-]+)\}\}", sub, text)


def build_palette(t: dict[str, str]) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.Window: t["bg"], QPalette.WindowText: t["text"], QPalette.Base: t["surface"],
        QPalette.AlternateBase: t["surface-2"], QPalette.Text: t["text"], QPalette.Button: t["surface"],
        QPalette.ButtonText: t["text"], QPalette.ToolTipBase: t["surface-2"], QPalette.ToolTipText: t["text"],
        QPalette.Highlight: t["primary"], QPalette.HighlightedText: t["on-primary"],
        QPalette.Link: t["primary-text"], QPalette.PlaceholderText: t["text-muted"],
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(t["text-muted"]))
    return p


# ---------- system theme detection ----------
def parse_gsettings(color_scheme: str | None, gtk_theme: str | None) -> str | None:
    """-> 'dark' | 'light' | None."""
    cs = (color_scheme or "").strip("' \n")
    if cs == "prefer-dark":
        return "dark"
    if cs in ("prefer-light",):
        return "light"
    gt = (gtk_theme or "").strip("' \n").lower()
    if gt:
        return "dark" if "dark" in gt else "light"
    return None


def _gsettings(schema: str, key: str) -> str | None:
    try:
        r = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True, timeout=2)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def system_scheme() -> str:
    scheme = QGuiApplication.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return "dark"
    if scheme == Qt.ColorScheme.Light:
        return "light"
    for schema in ("org.cinnamon.desktop.interface", "org.gnome.desktop.interface"):
        got = parse_gsettings(_gsettings(schema, "color-scheme"), _gsettings(schema, "gtk-theme"))
        if got:
            return got
    return "light"


def resolve(mode: str, system: str) -> str:
    return mode if mode in ("light", "dark") else system


def load_fonts(app: QApplication) -> str | None:
    """Load the bundled Inter (Regular + SemiBold); None on failure (system font is used)."""
    from PySide6.QtGui import QFont, QFontDatabase
    d = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    families: list[str] = []
    for name in ("Inter-Regular.ttf", "Inter-SemiBold.ttf"):
        fid = QFontDatabase.addApplicationFont(str(d / name)) if (d / name).exists() else -1
        if fid >= 0:
            families += QFontDatabase.applicationFontFamilies(fid)
    if "Inter" in families:
        app.setFont(QFont("Inter", 10))
        return "Inter"
    return None


class ThemeManager(QObject):
    changed = Signal(str)   # 'light' | 'dark' (the resolved result)

    def __init__(self, app: QApplication, mode: str = "system", cache_dir: Path | None = None):
        super().__init__(app)
        self.app, self.mode, self.current = app, mode, "light"
        self.cache_dir = cache_dir or Path(__import__("tempfile").gettempdir()) / "pipanel-theme"
        app.setStyle("Fusion")
        QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self._system_changed())
        self._poll = QTimer(self, interval=4000)   # fallback when Qt does not report the theme (Cinnamon)
        self._poll.timeout.connect(self._system_changed)
        self.apply(mode)

    def tokens(self) -> dict[str, str]:
        return TOKENS[self.current]

    def apply(self, mode: str) -> str:
        from .icons import write_arrow_files
        self.mode = mode if mode in ("system", "light", "dark") else "system"
        self.current = resolve(self.mode, system_scheme())
        t = TOKENS[self.current]
        extra = write_arrow_files(self.cache_dir, t)
        self.app.setPalette(build_palette(t))
        self.app.setStyleSheet(render_qss(t, extra))
        if self.mode == "system" and QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Unknown:
            self._poll.start()
        else:
            self._poll.stop()
        global _current
        _current = self.current
        self.changed.emit(self.current)
        bus.changed.emit(self.current)
        return self.current

    def _system_changed(self) -> None:
        if self.mode == "system" and resolve("system", system_scheme()) != self.current:
            self.apply("system")

    def toggle(self) -> str:
        """Header button: switch between light and dark (leaves follow-system mode)."""
        self.apply("dark" if self.current == "light" else "light")
        return self.mode
