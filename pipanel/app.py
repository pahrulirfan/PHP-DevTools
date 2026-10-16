from __future__ import annotations

import sys

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .core.controller import Controller
from .ui.icons import logo_icon
from .ui.main_window import MainWindow
from .ui.theme import ThemeManager, load_fonts


def create(ctl: Controller | None = None, app: QApplication | None = None):
    app = app or QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("pipanel")
    app.setApplicationDisplayName("PIPanel")
    app.setDesktopFileName("pipanel")
    ctl = ctl or Controller()
    load_fonts(app)
    themer = ThemeManager(app, ctl.cfg.theme)
    win = MainWindow(ctl, themer)
    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(logo_icon(), app)
        tray.setToolTip("PIPanel")
        menu = QMenu()
        for text, fn in (("Open PIPanel", lambda: (win.showNormal(), win.activateWindow())),
                         ("Start all services", lambda: win.tasks.run(ctl.start_all)),
                         ("Stop all services", lambda: win.tasks.run(ctl.stop_all))):
            a = QAction(text, menu)
            a.triggered.connect(lambda _=False, f=fn: f())
            menu.addAction(a)
        menu.addSeparator()
        quit_a = QAction("Quit", menu)

        def quit_():
            win.quitting = True
            app.quit()
        quit_a.triggered.connect(quit_)
        menu.addAction(quit_a)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda r: win.showNormal() if r == QSystemTrayIcon.Trigger else None)
        tray.show()
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
