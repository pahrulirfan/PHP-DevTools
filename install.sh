#!/usr/bin/env bash
# Install PIPanel for this user: venv + PySide6, application menu launcher, and the root helper (sudo once).
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

LAUNCHER_ONLY=0
[ "${1:-}" = "--launcher-only" ] && LAUNCHER_ONLY=1   # refresh only the menu entry and icons (no venv, no sudo)

if [ "$LAUNCHER_ONLY" = 0 ]; then
  if ! python3 -c 'import ensurepip, venv' 2>/dev/null; then
    echo "The python3-venv package is missing. Run: sudo apt install -y python3-venv python3-pip libxcb-cursor0" >&2
    exit 1
  fi
  echo "==> Creating the virtualenv and installing dependencies"
  python3 -m venv --clear .venv   # rebuilt from scratch, so it also works after the folder was moved or renamed
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -e '.[ui]'
fi

echo "==> Installing the launcher (application menu + icon)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS="$DATA/applications"
HICOLOR="$DATA/icons/hicolor"
mkdir -p "$APPS" "$HICOLOR/scalable/apps"
cp pipanel/assets/pipanel.svg "$HICOLOR/scalable/apps/pipanel.svg"
.venv/bin/python -m pipanel.ui.icons "$HICOLOR" >/dev/null   # PNGs at 16..256 px
# Absolute icon path: the menu finds it even when an old icon-theme.cache would hide a newly added icon.
# Write a temp file and rename it: the menu watches this folder and may read a half-written file, then ignore the
# launcher until the next login.
sed -e "s|@ROOT@|$ROOT|g" -e "s|@ICON@|$HICOLOR/scalable/apps/pipanel.svg|g" packaging/pipanel.desktop > "$APPS/.pipanel.desktop.tmp"
mv -f "$APPS/.pipanel.desktop.tmp" "$APPS/pipanel.desktop"
# Rebuild the icon cache too (-t: no index.theme in the per-user folder), so theme lookups by name work.
gtk-update-icon-cache -f -t "$HICOLOR" >/dev/null 2>&1 || true
update-desktop-database "$APPS" 2>/dev/null || true

if [ "$LAUNCHER_ONLY" = 1 ]; then
  echo "Launcher refreshed. If the menu still shows the old icon, log out and in once."
  exit 0
fi

echo "==> Installing the root helper (needs sudo, only once)"
sudo install -d -m 755 -o root -g root /usr/lib/pipanel
sudo install -m 755 -o root -g root packaging/pipanel-helper /usr/lib/pipanel/pipanel-helper
sudo install -m 644 -o root -g root packaging/com.pipanel.helper.policy /usr/share/polkit-1/actions/com.pipanel.helper.policy

cat <<MSG

Done. Open "PIPanel" from the application menu (or: $ROOT/.venv/bin/pipanel),
then click "Run setup" to install Nginx, PHP, dnsmasq, and the certificates.
MSG
