from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from .core.controller import Controller
from .ui.main_window import MainWindow
from .ui.theme import ThemeManager, load_fonts
from .ui.tray import Tray


def create(ctl: Controller | None = None, app: QApplication | None = None):
    app = app or QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("pipanel")
    app.setApplicationDisplayName("PIPanel")
    app.setDesktopFileName("pipanel")
    ctl = ctl or Controller()
    try:
        ctl.install_cli_launcher(sys.executable)   # the `pipanel` command for terminals
    except OSError:
        pass
    load_fonts(app)
    themer = ThemeManager(app, ctl.cfg.theme)
    win = MainWindow(ctl, themer)
    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = Tray(ctl, win, app, win.monitor)
        tray.show()
        win.notifier.tray_icon = tray.icon   # fallback when notify-send is missing
        win.tray_available = True
    return app, win, tray


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    app, win, tray = create()
    app.setQuitOnLastWindowClosed(tray is None)
    if "--minimized" in argv and tray is not None:
        pass   # stay in the tray
    else:
        win.show()
    return app.exec()
