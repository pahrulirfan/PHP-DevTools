"""Thin-line SVG icons (Lucide style), colored through currentColor."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_P = {
    "sites": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18"/>',
    "php": '<path d="m8 8-5 4 5 4"/><path d="m16 8 5 4-5 4"/><path d="m14 5-4 14"/>',
    "node": '<path d="M12 2.5 20 7v10l-8 4.5L4 17V7z"/><path d="m4 7 8 4.5L20 7"/><path d="M12 11.5v10"/>',
    "mysql": '<ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v13c0 1.7 3.6 3 8 3s8-1.3 8-3v-13"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    "share": '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4"/><path d="m15.4 6.5-6.8 4"/>',
    "services": '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01"/>',
    "logs": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/>',
    "settings": '<path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "sliders": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "folder-plus": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 11v5M9.5 13.5h5"/>',
    "external": '<path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "code": '<path d="m8 8-5 4 5 4"/><path d="m16 8 5 4-5 4"/>',
    "terminal": '<path d="m5 8 4 4-4 4"/><path d="M12 17h7"/><rect x="2.5" y="3.5" width="19" height="17" rx="2.5"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/><path d="M9 7V4h6v3"/>',
    "play": '<path d="M7 4.5v15l12-7.5z"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.5-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.5 4.5L20 16"/><path d="M20 20v-4h-4"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7"/>',
    "alert": '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17.5h.01"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "stethoscope": '<path d="M6 3v6a5 5 0 0 0 10 0V3"/><path d="M11 14v2a4 4 0 0 0 8 0v-2"/><circle cx="19" cy="12" r="2"/>',
    "server": '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 6.5 8.5 6.5 8.5-6.5"/>',
    "download": '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/>',
    "upload": '<path d="M12 15V4"/><path d="m7 9 5-5 5 5"/><path d="M5 20h14"/>',
    "plug": '<path d="M9 3v5M15 3v5"/><path d="M6 8h12v3a6 6 0 0 1-12 0z"/><path d="M12 17v4"/>',
    "rocket": '<path d="M5 15c-1.5 1-2 4-2 6 2 0 5-.5 6-2"/><path d="M12 15 9 12c1-4 4-8 11-9 0 7-5 10-9 11z"/><circle cx="15" cy="9" r="1.5"/>',
}


def svg(name: str, color: str = "currentColor", width: float = 1.8) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{_P[name]}</svg>')


def pixmap(name: str, color: str, size: int = 18) -> QPixmap:
    dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    img = QImage(int(size * dpr), int(size * dpr), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    QSvgRenderer(QByteArray(svg(name, color).encode())).render(p)
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str, size: int = 18) -> QIcon:
    return QIcon(pixmap(name, color, size))


def dot_icon(color: str, size: int = 16) -> QIcon:
    """A filled status dot (tray menu)."""
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    r = size * 0.3
    p.drawEllipse(QPointF(size / 2, size / 2), r, r)
    p.end()
    return QIcon(QPixmap.fromImage(img))


GLYPH = ('<path d="M23 47V17h11.5a10.5 10.5 0 0 1 0 21H23" stroke="#fff" stroke-width="6.5" '
         'stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
         '<circle cx="46.5" cy="47" r="4.6" fill="#2DD4BF"/>')   # the teal dot doubles as a status dot


def logo_svg() -> str:
    """App logo: purple squircle with a soft gradient, a white "P", and a teal status dot."""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<defs><linearGradient id="g" x1="8" y1="2" x2="56" y2="62" gradientUnits="userSpaceOnUse">'
            '<stop offset="0" stop-color="#9F6BFF"/><stop offset="1" stop-color="#5B21B6"/></linearGradient></defs>'
            '<rect width="64" height="64" rx="15" fill="url(#g)"/>'
            '<rect x=".75" y=".75" width="62.5" height="62.5" rx="14.3" fill="none" stroke="#fff" '
            'stroke-opacity=".22" stroke-width="1.5"/>' + GLYPH + '</svg>')


def logo_pixmap(size: int) -> QPixmap:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(logo_svg().encode())).render(p)
    p.end()
    return QPixmap.fromImage(img)


def export_icons(root, sizes=(16, 22, 24, 32, 48, 64, 128, 256)) -> list:
    """Write hicolor PNGs (<root>/<N>x<N>/apps/pipanel.png) for launchers that do not render SVG well."""
    from pathlib import Path
    out = []
    for size in sizes:
        d = Path(root) / f"{size}x{size}" / "apps"
        d.mkdir(parents=True, exist_ok=True)
        img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        QSvgRenderer(QByteArray(logo_svg().encode())).render(p)
        p.end()
        img.save(str(d / "pipanel.png"))
        out.append(d / "pipanel.png")
    return out


def logo_icon() -> QIcon:
    ic = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):   # crisp at every size (tray, taskbar, window, launcher)
        ic.addPixmap(logo_pixmap(size))
    return ic


def write_arrow_files(cache: Path, tokens: dict[str, str]) -> dict[str, str]:
    """Combo box arrow for QSS (needs a file), colored to match the theme."""
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"arrow-down-{tokens['text-muted'].lstrip('#')}.png"
    if not f.exists():
        pixmap("chevron-down", tokens["text-muted"], 12).save(str(f))
    chk = cache / "check-white.png"
    if not chk.exists():
        pixmap("check", "#FFFFFF", 14).save(str(chk))
    return {"arrow_down": str(f), "check_icon": str(chk)}


if __name__ == "__main__":   # python -m pipanel.ui.icons <hicolor dir>   (used by install.sh)
    import os
    import sys
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    for f in export_icons(sys.argv[1]):
        print(f)
