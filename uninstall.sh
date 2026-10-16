#!/usr/bin/env bash
# Remove PIPanel for this user. System packages (nginx, php, mysql) are NOT removed.
set -uo pipefail
cd "$(dirname "$0")"

echo "==> Stopping and removing user services"
for u in "$HOME"/.config/systemd/user/pipanel-*.service; do
  [ -e "$u" ] || continue
  n="$(basename "$u")"
  systemctl --user disable --now "$n" 2>/dev/null
  rm -f "$u"
done
systemctl --user daemon-reload

echo "==> Removing shell integration and autostart"
[ -x .venv/bin/python ] && .venv/bin/python - <<'PY'
import os
from pathlib import Path
from pipanel.core.paths import Paths
from pipanel.core.node import Node, ShellIntegration
from pipanel.core.runner import Runner
p = Paths.from_env()
ShellIntegration(p, Path(os.environ["HOME"]), Node(p, Runner())).remove()
PY
rm -f "$HOME/.config/autostart/pipanel.desktop"
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/pipanel.desktop"
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/pipanel.svg"

echo "==> Removing system files (sudo)"
sudo rm -f /usr/lib/pipanel/pipanel-helper /usr/share/polkit-1/actions/com.pipanel.helper.policy \
  /etc/sysctl.d/60-pipanel.conf /etc/systemd/resolved.conf.d/pipanel.conf \
  /usr/local/share/ca-certificates/pipanel-mkcert.crt /etc/apparmor.d/local/usr.sbin.mysqld
sudo rmdir /usr/lib/pipanel 2>/dev/null
sudo systemctl restart systemd-resolved 2>/dev/null
sudo update-ca-certificates --fresh >/dev/null 2>&1

echo "Done. Data remains in ~/.config/pipanel and ~/.local/share/pipanel (delete manually if you want)."
