"""Shrink a PySide6-Essentials install to what PIPanel uses (QtCore, QtGui, QtWidgets, QtSvg and the plugins a
desktop needs), then prove with ldd that nothing that is kept misses a library.

    python3 prune_qt.py <site-packages> [--report deps.txt]

Qt libraries are not removed by name: everything that the kept bindings and plugins load (directly or through
other Qt libraries) stays, everything else goes.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

KEEP_BINDINGS = ("QtCore", "QtGui", "QtWidgets", "QtSvg")
# plugin folder -> file name patterns to keep (None keeps the whole folder)
KEEP_PLUGINS = {
    "platforms": ("libqxcb.so", "libqwayland*.so", "libqoffscreen.so"),
    "platformthemes": None,           # GTK and XDG portal: native file dialogs and colors
    "platforminputcontexts": None,    # input methods (ibus, compose keys)
    "imageformats": None,
    "iconengines": None,              # SVG icons
    "xcbglintegrations": None,
    "wayland-decoration-client": None,
    "wayland-graphics-integration-client": None,
    "wayland-shell-integration": None,
}
DROP_DIRS = ("qml", "translations", "include", "typesystems", "scripts", "glue", "doc", "metatypes", "resources")


def elf(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def ldd(path: Path) -> dict[str, str | None]:
    """soname -> resolved path (None when not found)."""
    out = subprocess.run(["ldd", str(path)], capture_output=True, text=True).stdout
    deps: dict[str, str | None] = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\S+) => (not found|(\S+))", line)
        if m:
            deps[m.group(1)] = None if m.group(2) == "not found" else m.group(3)
    return deps


def prune(site: Path) -> list[Path]:
    pyside = site / "PySide6"
    qt = pyside / "Qt"
    # 1. Python bindings, tools and data that PIPanel never loads
    for f in pyside.iterdir():
        if (f.name.endswith(".abi3.so") and f.name.split(".")[0] not in KEEP_BINDINGS) \
                or f.name.startswith("libpyside6qml"):   # QML support, only loaded by QtQml
            f.unlink()
        elif f.suffix == ".pyi" or (f.is_file() and elf(f) and not f.name.endswith(".so") and ".so." not in f.name):
            f.unlink()   # type stubs; designer, linguist, qmlls and other tools
    for d in DROP_DIRS:
        shutil.rmtree(pyside / d, ignore_errors=True)
        shutil.rmtree(qt / d, ignore_errors=True)
    shutil.rmtree(qt / "libexec", ignore_errors=True)
    for f in (qt / "bin").glob("*") if (qt / "bin").is_dir() else []:
        f.unlink()
    plugins = qt / "plugins"
    for d in plugins.iterdir():
        keep = KEEP_PLUGINS.get(d.name, False)
        if keep is False:
            shutil.rmtree(d)
        elif keep is not None:
            for f in d.iterdir():
                if not any(f.match(p) for p in keep):
                    f.unlink()

    # plugins that load a Qt library Essentials does not ship (Pdf, VirtualKeyboard) never worked: drop them
    for f in list(plugins.rglob("*.so")):
        if any(path is None and soname.startswith("libQt6") for soname, path in ldd(f).items()):
            print(f"dropping {f.relative_to(plugins)}: it needs a Qt module PySide6-Essentials does not ship")
            f.unlink()

    # 2. Qt libraries: keep the closure of what the remaining ELF files load
    roots = [f for f in pyside.rglob("*") if f.is_file() and elf(f) and qt / "lib" not in f.parents]
    roots += [f for f in (site / "shiboken6").rglob("*") if f.is_file() and elf(f)]
    libdir = (qt / "lib").resolve()
    needed: set[Path] = set()
    todo = list(roots)
    while todo:
        for path in ldd(todo.pop()).values():
            if path and Path(path).resolve().parent == libdir and Path(path).resolve() not in needed:
                needed.add(Path(path).resolve())
                todo.append(Path(path).resolve())
    for f in (qt / "lib").iterdir():
        if f.is_file() and elf(f) and f.resolve() not in needed:
            f.unlink()
    for f in (qt / "lib").iterdir():   # symlinks to removed files, and pkg-config / cmake leftovers
        if (f.is_symlink() and not f.exists()) or f.is_dir():
            shutil.rmtree(f) if f.is_dir() and not f.is_symlink() else f.unlink()
    return roots + sorted(needed)


def needed(path: Path) -> set[str]:
    """Libraries a file names directly (its NEEDED entries); ldd also lists what those load in turn."""
    out = subprocess.run(["objdump", "-p", str(path)], capture_output=True, text=True).stdout
    return set(re.findall(r"^\s*NEEDED\s+(\S+)", out, re.M))


def check(files: list[Path]) -> set[str]:
    """Fail when a kept file misses a library; return the system libraries they load directly (the package
    manager brings in what those need)."""
    missing, system = [], set()
    for f in files:
        direct = needed(f)
        for soname, path in ldd(f).items():
            if path is None:
                missing.append(f"{f.name}: {soname}")
            elif "/site-packages/" not in path and soname in direct:
                system.add(path)
    if missing:
        sys.exit("missing libraries after pruning:\n  " + "\n  ".join(sorted(set(missing))))
    return system


if __name__ == "__main__":
    site = Path(sys.argv[1])
    files = prune(site)
    system = check([f for f in files if f.exists()])
    if "--report" in sys.argv:
        Path(sys.argv[sys.argv.index("--report") + 1]).write_text("\n".join(sorted(system)) + "\n")
    print(f"kept {len(files)} ELF files, {len(system)} system libraries")
