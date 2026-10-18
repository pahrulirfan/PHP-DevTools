#!/usr/bin/env bash
# Build pipanel_<version>~<distro>_amd64.deb. Run as root inside a clean Ubuntu container of the target release
# (the package carries its own Python environment, which is tied to that release's Python version):
#
#   docker run --rm -v "$PWD:/src" -w /src ubuntu:24.04 packaging/build-deb.sh 0.2.0 dist
#
# The app goes to /opt/pipanel (a virtualenv with PIPanel, Jinja2 and a trimmed Qt), the root helper and its
# polkit policy to the same places install.sh uses, and `pipanel` to /usr/bin.
set -euo pipefail
PKG_VERSION="${1:?usage: build-deb.sh <version> [output dir]}"   # not VERSION: /etc/os-release sets that
OUT="$(mkdir -p "${2:-dist}" && cd "${2:-dist}" && pwd)"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/pipanel
export DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1

# Qt's runtime libraries: needed here so that ldd can resolve them and name their packages
QT_RUNTIME="libegl1 libgl1 libopengl0 libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libfreetype6 libdbus-1-3
  libglib2.0-0 libx11-6 libx11-xcb1 libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1
  libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1 libxcb-sync1
  libxcb-shm0 libxcb-render0 libwayland-client0 libwayland-cursor0 libwayland-egl1 libgtk-3-0"
echo "==> Installing build tools"
apt-get update -qq
# shellcheck disable=SC2086
apt-get install -y -qq --no-install-recommends python3 python3-venv ca-certificates file binutils $QT_RUNTIME >/dev/null

. /etc/os-release
PY=$(python3 -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')
DEB="pipanel_${PKG_VERSION}~ubuntu${VERSION_ID}_amd64.deb"

echo "==> Creating $APP with $PY"
rm -rf "$APP"
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q --no-compile "$SRC[ui]"
SITE="$("$APP/venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"

echo "==> Trimming Qt to what PIPanel uses"
python3 "$SRC/packaging/prune_qt.py" "$SITE" --report /tmp/system-libs.txt
# pip itself is not needed at run time (careful: "pip*" would also match the pipanel command)
rm -rf "$APP/venv/bin/pip" "$APP/venv/bin/pip3" "$APP/venv/bin/pip${PY#python}" \
       "$SITE"/pip "$SITE"/pip-* "$SITE"/setuptools* "$SITE"/_distutils_hack "$SITE"/pkg_resources
test -x "$APP/venv/bin/pipanel" || { echo "the pipanel command is missing from the venv" >&2; exit 1; }

echo "==> Checking that the app starts with the trimmed Qt"
HOME=$(mktemp -d) XDG_RUNTIME_DIR=$(mktemp -d) QT_QPA_PLATFORM=offscreen "$APP/venv/bin/pipanel" _smoke
"$APP/venv/bin/python" -m compileall -q "$SITE" >/dev/null || true

echo "==> Assembling the package tree"
STAGE=$(mktemp -d)
mkdir -p "$STAGE/opt" "$STAGE/usr/bin" "$STAGE/usr/lib/pipanel" "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/polkit-1/actions" "$STAGE/usr/share/icons/hicolor/scalable/apps" "$STAGE/DEBIAN"
cp -a "$APP" "$STAGE/opt/"
ln -s "$APP/venv/bin/pipanel" "$STAGE/usr/bin/pipanel"
install -m 755 "$SRC/packaging/pipanel-helper" "$STAGE/usr/lib/pipanel/pipanel-helper"
install -m 644 "$SRC/packaging/com.pipanel.helper.policy" "$STAGE/usr/share/polkit-1/actions/"
install -m 644 "$SRC/pipanel/assets/pipanel.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/pipanel.svg"
QT_QPA_PLATFORM=offscreen "$APP/venv/bin/python" -m pipanel.ui.icons "$STAGE/usr/share/icons/hicolor" >/dev/null
sed -e "s|^Exec=.*|Exec=/usr/bin/pipanel|" -e "s|^Icon=.*|Icon=pipanel|" "$SRC/packaging/pipanel.desktop" \
    > "$STAGE/usr/share/applications/pipanel.desktop"
chmod 644 "$STAGE/usr/share/applications/pipanel.desktop"

echo "==> Working out the dependencies"
deps=()
while read -r lib; do
  [ -z "$lib" ] && continue
  pkg=""
  for p in "$lib" "$(readlink -f "$lib")" "${lib/#\/lib\//\/usr\/lib\/}"; do
    pkg=$(dpkg -S "$p" 2>/dev/null | head -1 | cut -d: -f1) && [ -n "$pkg" ] && break
  done
  [ -n "${pkg:-}" ] && deps+=("$pkg")
done < /tmp/system-libs.txt
# libc6 and friends come with every system; keep the list to what a desktop may lack
mapfile -t deps < <(printf '%s\n' "${deps[@]}" | grep -vE '^(libc6|libgcc-s1|libstdc\+\+6|zlib1g)$' | sort -u)
DEPENDS="$PY, $(IFS=,; echo "${deps[*]}" | sed 's/,/, /g'), pkexec | policykit-1"
SIZE=$(du -sk "$STAGE" --exclude=DEBIAN | cut -f1)

cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: pipanel
Version: ${PKG_VERSION}~ubuntu${VERSION_ID}
Architecture: amd64
Maintainer: Pahrul Irfan <7369642+pahrulirfan@users.noreply.github.com>
Installed-Size: $SIZE
Depends: $DEPENDS
Recommends: git, curl
Section: devel
Priority: optional
Homepage: https://github.com/pahrulirfan/PHP-DevTools
Description: Local PHP development for Linux, without Docker
 PIPanel runs Nginx, several PHP versions, MySQL or MariaDB, Mailpit, Redis, trusted
 HTTPS and *.test domains for Laravel and PHP projects, as services of your own user.
 Open PIPanel from the application menu and click "Run setup" once.
CONTROL
cat > "$STAGE/DEBIAN/postinst" <<'SCRIPT'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
  update-desktop-database -q /usr/share/applications 2>/dev/null || true
  gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
fi
SCRIPT
cat > "$STAGE/DEBIAN/postrm" <<'SCRIPT'
#!/bin/sh
set -e
# Services, settings and data of each user stay in their home folder; see the README to remove them.
if [ "$1" = remove ] || [ "$1" = purge ]; then
  update-desktop-database -q /usr/share/applications 2>/dev/null || true
fi
SCRIPT
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$OUT/$DEB" >/dev/null
echo "==> $OUT/$DEB ($(du -h "$OUT/$DEB" | cut -f1))"
echo "    Depends: $DEPENDS"
