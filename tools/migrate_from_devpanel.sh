#!/usr/bin/env bash
# One-time migration from the old "DevPanel" name to PIPanel. Safe to run more than once.
# Moves your settings, certificates and Node versions, removes the old launcher/shell blocks, and renames the
# system files that the old setup created (needs sudo). Run it BEFORE ./install.sh.
set -uo pipefail

CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}"

echo "==> Stopping and removing old user services"
for u in "$CFG"/systemd/user/devpanel-*.service; do
  [ -e "$u" ] || continue
  systemctl --user disable --now "$(basename "$u")" 2>/dev/null
  rm -f "$u"
done
systemctl --user daemon-reload 2>/dev/null

echo "==> Moving your data (settings, certificates, Node versions)"
move() {
  if [ -d "$1" ] && [ ! -e "$2" ]; then mv "$1" "$2" && echo "    $1 -> $2"; fi
}
move "$CFG/devpanel" "$CFG/pipanel"
move "$DATA/devpanel" "$DATA/pipanel"
move "$STATE/devpanel" "$STATE/pipanel"
# Absolute paths inside the moved files still say "devpanel": fix wrappers and fnm alias symlinks.
for w in "$DATA/pipanel/bin/php" "$DATA/pipanel/bin/composer"; do
  [ -f "$w" ] && sed -i 's#/devpanel/#/pipanel/#g' "$w"
done
if [ -d "$DATA/pipanel/fnm" ]; then
  find "$DATA/pipanel/fnm" -type l | while read -r l; do
    t="$(readlink "$l")"
    case "$t" in */devpanel/*) ln -sfn "${t//\/devpanel\//\/pipanel\/}" "$l" ;; esac
  done
fi

echo "==> Removing the old shell blocks, launcher and autostart entry"
for f in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
  [ -f "$f" ] || continue
  python3 - "$f" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
new = re.sub(r"\n?# >>> devpanel >>>.*?# <<< devpanel <<<\n?", "\n", s, flags=re.S)
if new != s:
    open(p, "w").write(new.rstrip("\n") + "\n")
    print("    cleaned", p)
PY
done
rm -f "$DATA/applications/devpanel.desktop" "$DATA/icons/hicolor/scalable/apps/devpanel.svg" \
      "$CFG/autostart/devpanel.desktop"
update-desktop-database "$DATA/applications" 2>/dev/null || true

if [ "${PIPANEL_MIGRATE_SKIP_SUDO:-0}" = "1" ]; then
  echo "==> Skipping system files (PIPANEL_MIGRATE_SKIP_SUDO=1)"
  exit 0
fi

echo "==> Renaming the old system files (sudo)"
sudo bash -c '
  ren() { [ -f "$1" ] && mv -f "$1" "$2" && echo "    $1 -> $2"; return 0; }
  ren /etc/sysctl.d/60-devpanel.conf /etc/sysctl.d/60-pipanel.conf
  ren /etc/systemd/resolved.conf.d/devpanel.conf /etc/systemd/resolved.conf.d/pipanel.conf
  ren /usr/local/share/ca-certificates/devpanel-mkcert.crt /usr/local/share/ca-certificates/pipanel-mkcert.crt
  rm -f /usr/share/polkit-1/actions/com.devpanel.helper.policy /usr/lib/devpanel/devpanel-helper
  rmdir /usr/lib/devpanel 2>/dev/null
  if [ -f /etc/apparmor.d/local/usr.sbin.mysqld ] && grep -q "^# DevPanel" /etc/apparmor.d/local/usr.sbin.mysqld; then
    rm -f /etc/apparmor.d/local/usr.sbin.mysqld
  fi
  update-ca-certificates --fresh >/dev/null 2>&1
  systemctl restart systemd-resolved 2>/dev/null
  true
'
echo "Done. Next: ./install.sh, then open PIPanel and click Run setup."
