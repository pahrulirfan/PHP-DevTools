# PIPanel

A desktop app (PySide6) for Laravel/PHP development on Linux Mint, in the spirit of Laravel Herd:
Nginx + several PHP-FPM versions + dnsmasq (`*.test`) + HTTPS (mkcert) + MySQL 8 or MariaDB + Node (fnm) + ngrok,
without Docker and without daily `sudo`. All services run as your own user through `systemctl --user`.

Plan and design decisions: [PLAN.md](PLAN.md).

## Install

```
sudo apt install -y python3-venv python3-pip libxcb-cursor0
./install.sh        # venv, launcher, and the root helper (sudo once)
```

Open **PIPanel** from the application menu, click **Run setup**, then choose your projects folder
(for example `~/PHPProject`). Uninstall with `./uninstall.sh`.

### Coming from the old "DevPanel" name

If you installed this project before it was renamed, run the one-time migration first (it moves your settings,
certificates and Node versions, and renames the system files; needs `sudo`), then install as usual:

```
./tools/migrate_from_devpanel.sh
./install.sh
```

## How sites work

- **Projects folder**: pick the folder that holds all your projects. Every subfolder in it becomes a site,
  so `~/PHPProject/blog` is served as `https://blog.test`. You can add, change, or remove projects folders
  in *Settings*.
- **Link one folder**: serve a single folder under a name you choose, wherever it lives.
- **Web root**: PIPanel detects it (`public` for Laravel, `.` for WordPress and plain PHP). If your project
  uses something else, click the *Change web root* icon on the site row and enter the folder (for example
  `web` or `.`).

## Database and Laravel packages

- **Database**: choose MySQL 8 or MariaDB on the *Database* page (match your production server). They share port
  3306 so only one runs at a time, and each keeps its own data.
- **Laravel**: on the *Laravel* page pick a project and install packages such as Debugbar, Telescope, Livewire,
  Pest, or any package from Packagist. They are installed with Composer using that site's PHP version.

## Development

```
python3 -m venv .venv && .venv/bin/pip install -e '.[ui,dev]'
.venv/bin/pytest                       # no root and no Nginx needed
.venv/bin/python tools/screenshots.py  # offscreen screenshots into docs/screens/
.venv/bin/python -m pipanel
```

`pipanel/core` never imports Qt; the UI only calls `core.controller.Controller`.
