"""Open the real app, show every page and the tray menu, then quit. Checks a built package:

    QT_QPA_PLATFORM=offscreen pipanel _smoke

Exits non-zero when a page fails to build or Qt reports an error.
"""
import sys
import time


def main() -> int:
    errors = []
    sys.excepthook = lambda tp, val, tb: errors.append(f"{tp.__name__}: {val}")

    from PySide6.QtCore import QTimer

    from pipanel.app import create

    app, win, tray = create()
    win.resize(1120, 720)
    win.show()
    for i, page in enumerate(win.pages):
        win.show_page(i)
        for _ in range(20):
            win.tasks.wait(20)
            app.processEvents()
            time.sleep(0.005)
        if win.grab().isNull():
            errors.append(f"page {page.title} did not render")
    if tray is not None:
        tray.about_to_show()
    QTimer.singleShot(0, app.quit)
    app.exec()
    print(f"platform={app.platformName()} pages={len(win.pages)} tray={tray is not None} errors={len(errors)}")
    for e in errors:
        print("  " + e)
    return 1 if errors else 0


def safe_main() -> int:
    """main(), with a crash printed instead of lost (packaged apps have no console to show it)."""
    import traceback
    try:
        return main()
    except BaseException:  # noqa: BLE001
        traceback.print_exc()
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    sys.exit(safe_main())
