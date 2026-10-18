# PyInstaller build of PIPanel for Windows and macOS (Linux ships a .deb, see packaging/build-deb.sh).
#
#   pyinstaller packaging/pyinstaller/pipanel.spec
#
# Windows: dist/PIPanel/ with PIPanel.exe (no console) and pipanel-cli.exe (console: the `pipanel`, `php` and
# `composer` commands, and the background agent). macOS: dist/PIPanel.app.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent.parent
PKG = ROOT / "pipanel"
WINDOWS, MACOS = sys.platform.startswith("win"), sys.platform == "darwin"

datas = [
    (str(PKG / "templates"), "pipanel/templates"),
    (str(PKG / "assets"), "pipanel/assets"),
    (str(PKG / "ui" / "style.qss.tmpl"), "pipanel/ui"),
]
if WINDOWS:
    datas.append((str(ROOT / "packaging" / "windows" / "pipanel-helper.ps1"), "."))

# Qt modules PIPanel never loads
excludes = ["PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtNetwork", "PySide6.QtSql",
            "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPdf",
            "PySide6.QtConcurrent", "PySide6.QtPrintSupport", "PySide6.QtXml", "PySide6.QtDBus", "tkinter"]

a = Analysis([str(Path(SPECPATH) / "entry.py")], pathex=[str(ROOT)], datas=datas,
             hiddenimports=["pipanel.core.agent", "pipanel.core.dnsd", "pipanel.core.fcgipool", "pipanel.smoke"],
             excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)
icon_win = str(ROOT / "packaging" / "windows" / "pipanel.ico")
icon_mac = str(ROOT / "packaging" / "macos" / "pipanel.icns")

gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PIPanel", console=False,
          icon=icon_win if WINDOWS else (icon_mac if MACOS else None), argv_emulation=False)
exes = [gui]
if WINDOWS:   # the same program with a console, for terminals and for the services (no window either way)
    exes.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name="pipanel-cli", console=True, icon=icon_win))

coll = COLLECT(*exes, a.binaries, a.datas, name="PIPanel")

if MACOS:
    app = BUNDLE(coll, name="PIPanel.app", icon=icon_mac, bundle_identifier="com.pipanel.app",
                 info_plist={"CFBundleName": "PIPanel", "CFBundleDisplayName": "PIPanel",
                             "CFBundleShortVersionString": __import__("os").environ.get("PIPANEL_VERSION", "0.0.0"),
                             "LSMinimumSystemVersion": "12.0", "NSHighResolutionCapable": True,
                             "LSApplicationCategoryType": "public.app-category.developer-tools"})
