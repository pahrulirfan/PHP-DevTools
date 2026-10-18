# PIPanel: a "Herd" for Linux Mint (PySide6)

Development plan for a desktop app for Laravel/PHP developers on Linux Mint, with an experience like Laravel Herd on the Mac. The name **PIPanel** stands for **P**ahrul **I**rfan **Panel**.

Status: **Stage 1–5 code is complete (Linux, Windows, macOS), more than 400 automated tests pass, and it has been tested for real on Linux Mint 22.3** without root: Nginx 1.24 + PHP-FPM 8.3 + dnsmasq + **MySQL 8.0 and MariaDB 10.11** as `systemctl --user` units (HTTP, mkcert HTTPS, Laravel routing, version switch/reload, database login over TCP), Composer `require`/`remove` through a site's PHP, plus real downloads of Composer, fnm (+ Node 22), and ngrok. **Not yet tested** (needs root/GUI): `pkexec` + helper + polkit, `apt`/PPA, the `systemd-resolved` drop-in, database AppArmor, `mkcert -install` into NSS/system, and theme detection on Cinnamon. The real-test scripts are in `tools/e2e_*.py`.

---

## 1. Goals

- One GUI app to manage a local PHP development environment without Docker.
- Like the free version of Herd, plus MySQL and ngrok as requested.
- Simple: install once, then use it without touching the terminal.

## 2. Decisions already made

| Topic | Decision |
|---|---|
| UI technology | Python + PySide6 (Qt 6.5 or newer) |
| Target OS | Linux Mint 21.x / 22.x (Ubuntu 22.04 / 24.04 base), Cinnamon desktop |
| Approach | Native (no Docker): Nginx + PHP-FPM + dnsmasq |
| Running services | **systemd user units** (`systemctl --user`), not child processes of the app (see 4.0) |
| Database | **MySQL 8** (not MariaDB), run as a user-owned instance |
| Tunnel/share | **ngrok** (needs an account + authtoken) |
| Local domain | `*.test` |
| HTTPS | mkcert (optional: without it everything works over plain `http://`; only the HTTPS toggle is unavailable) |
| PHP versions | `ondrej/php` PPA packages |
| Composer | The official binary (`composer.phar`) owned by the user, not the apt package (too old) |
| Node.js | fnm |
| Order of work | Stage 1, then 2, then 3 (see section 8) |
| Look and feel | Modern and simple, purple main theme, Light / Dark / Follow system (see section 13) |

## 3. Features

### 3.1 Sites
- **Projects folder** (for example `~/PHPProject`): you pick the folder that holds all your projects, and every subfolder in it automatically becomes a site. `~/PHPProject/blog` is served as `blog.test`. Projects folders can be added, changed, and removed (Settings). (This is what Laravel Valet and Herd call "parking" a folder.)
- **Link one folder** with a name of your choice, wherever it lives.
- Site list: name, path, web root, PHP version, HTTPS status, project type.
- Buttons: open in the browser, open the folder, open in VS Code, open a terminal in that folder.
- HTTPS toggle per site.
- PHP version per site (isolation).
- **Web root** per site: auto-detected from the project type (`public` for Laravel, `.` for WordPress and plain PHP), and can be changed to any folder inside the project (for example `web`, `public_html`, or `.`).
- Create a new Laravel project from the app.

### 3.1b Laravel packages
- A **Laravel** page lists the Laravel sites; pick one and install packages ("plugins") into it with one click: Debugbar, IDE Helper, Telescope, Pint, Larastan, Livewire, Breeze, Sanctum, Socialite, Spatie Permission, Horizon, and Pest. Installed ones are detected from `composer.json` and can be removed.
- Any other package from Packagist can be installed by name (`vendor/package` or `vendor/package:^1.0`, optionally as a dev dependency). Names are validated; commands run without a shell.
- Composer and `artisan` run with **the site's own PHP version** and PIPanel's extension set, and the output is shown on the page. Nothing runs `migrate` automatically.

### 3.2 PHP
- Install / remove / update several versions. The list of versions is read from the PPA (currently 8.2 to 8.5; 8.1 has been EOL since the end of 2025 but can still be installed when available).
- Global default and per site.
- Switch the `php` version on the CLI through a user-owned wrapper (not `update-alternatives`, which needs root), see 4.2.
- A simple `php.ini` editor (`memory_limit`, `upload_max_filesize`, `max_execution_time`, `post_max_size`) and extension toggles, without touching `/etc/php`. A fresh install shows every extension ticked: that is how Ubuntu enables the modules of the installed PHP packages. Extensions needed by Laravel and Composer are shown in bold and ask for confirmation before being disabled; common extras (redis, imagick, xdebug, and so on) can be installed from a drop-down.
- Composer is installed automatically and can `self-update` without root.

### 3.3 Node.js
- Install, remove, and switch Node versions (fnm).
- Default version and per-project version (`.node-version` / `.nvmrc`).
- Automatic shell integration for bash and zsh.

### 3.4 Database (MySQL or MariaDB)
- Choose the engine: **MySQL 8** (default) or **MariaDB**, to match the database of your production server. They share port 3306, so only one runs at a time, and each keeps its own data folder (`~/.local/share/pipanel/mysql` or `.../mariadb`); switching does not convert databases.
- Start / stop / restart.
- Create and drop databases, create users.
- Change the root password, show connection info (host, port, user).
- A button to copy the Laravel `.env` settings (`DB_CONNECTION=mysql`, because Laravel 11+ defaults to SQLite).

### 3.5 Share (ngrok)
- ngrok needs an account: **it refuses to start a tunnel without a valid authtoken** (`ERR_NGROK_4018`; a malformed one gives `ERR_NGROK_105`; both checked against ngrok's servers with ngrok 3.39).
- The Share page has three states. *No token*: explanation plus links to the sign-up and token pages. *Saved, not verified*: a **Verify token** button starts ngrok briefly and waits until ngrok's servers accept the session (a tunnel is created) or reject it, and shows ngrok's own message. *Verified*: the token's fingerprint (never the token) is remembered, and only then is **Start sharing** enabled. Saving a new token resets the verification and runs the check automatically.
- The success path (a real, valid token) needs an account and has not been exercised; the failure paths have.
- Share a site with one click, show the public URL and a copy button. Stop sharing.

### 3.6 System
- Status panel for Nginx, PHP-FPM (per version), the database, DNS: start / stop / restart.
- System tray and an "open at login" option (for the app window; the services have their own autostart option through systemd user).
- Log viewer (Nginx access/error, PHP-FPM, MySQL).
- Doctor: checks for conflicting ports 80/443/3306, stopped services, `.test` not resolving, untrusted certificates, and system services that became active again.

## 4. Architecture

Principle: **every daily process runs as the normal user**. Root is only used for the first-time setup and when installing/removing packages.

```
PIPanel (PySide6)
 ├─ core/          logic without UI (testable without Qt)
 ├─ ui/            Qt views
 └─ systemd --user (units written by the app, all owned by the user):
     ├─ pipanel-nginx.service           ports 80 / 443
     ├─ pipanel-php8.x.service          one master per version, unix socket
     ├─ pipanel-dns.service             dnsmasq 127.0.0.1:5354, *.test -> 127.0.0.1
     ├─ pipanel-mysql.service           user-owned MySQL or MariaDB instance, port 3306
     └─ pipanel-ngrok.service           while sharing (one share at a time, matching the free-plan limit)
```

### 4.0 Why systemd user units instead of QProcess
If services ran as `QProcess` children, closing or crashing the app would kill nginx, PHP, and MySQL with it. With `systemctl --user` units:
- Services keep running when the window is closed, and can be enabled at login (`systemctl --user enable`).
- `Restart=on-failure`, status, and logs (`journalctl --user -u pipanel-*`) come for free.
- No root is needed. Unit files live in `~/.config/systemd/user/`.

One-shot commands (`systemctl --user ...`, `composer`, `mkcert`, `pkexec`) run through `core/runner.py` (subprocess) in a thread pool (`ui/tasks.py`) so the UI never freezes. Every service runs in the foreground (`daemon off;`, `--nodaemonize`, and so on) with `Type=simple`.

### 4.1 Ports 80 and 443 without root
One-time setup: `net.ipv4.ip_unprivileged_port_start=80` in `/etc/sysctl.d/60-pipanel.conf`, then `sysctl --system` so it applies immediately. Nginx can then run as the user and keeps working after package updates (unlike `setcap`, which is lost when nginx is updated). Note: this lets every local user bind port 80 and above, which is fine for a personal laptop. The ports are configurable (`http_port` / `https_port` in `config.json`), which is also how the real tests ran on unprivileged ports.

### 4.2 User-owned PHP-FPM and PHP CLI
The system service `php8.x-fpm` is not used. The app writes its own config and the unit runs:

```
php-fpm8.3 --nodaemonize --fpm-config ~/.config/pipanel/php/8.3/php-fpm.conf
```

- Socket: `$XDG_RUNTIME_DIR/pipanel/php8.3.sock` (usually `/run/user/1000/pipanel/`).
- The pool runs as the user, so there are no file-permission problems in the project folders. Do not write `user`/`group` directives in the pool (they are ignored with a warning when not root). `pid` and `error_log` point into the user's folders.
- The bundled system service is stopped and disabled during setup, and **every time** a new PHP version is installed (the `php8.x-fpm` package enables it automatically).

**`php.ini` settings and extensions without root.** `/etc/php` is owned by root, so the app uses its own scan folder per version through `PHP_INI_SCAN_DIR`:
- `~/.config/pipanel/php/8.3/conf.d/` holds symlinks to `/etc/php/8.3/mods-available/*.ini` for the enabled extensions, plus `99-pipanel.ini` for values such as `memory_limit`.
- Toggling an extension = adding/removing a symlink. An extension whose package is not installed still needs `apt` (through the root helper).
- FPM: `Environment=PHP_INI_SCAN_DIR=...` in the systemd unit.
- CLI: `php` in `~/.local/share/pipanel/bin/` is a **small wrapper script** (not a symlink) that sets `PHP_INI_SCAN_DIR` and then runs `exec /usr/bin/php8.x "$@"`. Switching the CLI version = rewriting the wrapper. Possible later: the wrapper reads the site's PHP version when run inside a site folder (like Herd).

**PHP packages per version** (the Laravel basics): `php8.x-fpm php8.x-cli php8.x-mbstring php8.x-xml php8.x-curl php8.x-zip php8.x-mysql php8.x-sqlite3 php8.x-bcmath php8.x-intl php8.x-gd`.

### 4.3 User-owned Nginx
- Its own main config (`~/.config/pipanel/nginx/nginx.conf`) with `daemon off;`, and `pid`, `error_log`, and every `*_temp_path` pointing into the user's folders. No `user` directive.
- Run it with `nginx -p ~/.config/pipanel/nginx -c nginx.conf -e stderr`. The `-e` option is needed so nginx does not try to open `/var/log/nginx/error.log` (owned by root) before the config is read.
- Relative `include`s are resolved from the user's config folder, so `mime.types` and `fastcgi_params` are written as absolute paths (`/etc/nginx/mime.types`, `/etc/nginx/fastcgi_params`).
- Includes `sites/*.conf`, one file per site, generated from a template. Plus a default server (`default_server`) that shows a "site not found" page for unknown `*.test` names.
- Every change: `nginx -t` first, then reload (`systemctl --user reload pipanel-nginx`, which runs `nginx -s reload` with the same `-p`/`-c`). A config that fails the test is rolled back.
- The system nginx service is stopped.

### 4.4 DNS `.test`
- Use the **`dnsmasq-base`** package (binary only, no service). The full `dnsmasq` package immediately starts a service on port 53 and clashes with `systemd-resolved`. `dnsmasq-base` is usually already installed because NetworkManager uses it.
- Run it on **`127.0.0.1:5354`**, not 5353 (port 5353 is used by Avahi/mDNS, which is on by default on Mint):

  ```
  dnsmasq --keep-in-foreground --conf-file=~/.config/pipanel/dns/dnsmasq.conf
  ```

  Config contents: `port=5354`, `listen-address=127.0.0.1`, `bind-interfaces`, `no-resolv`, `no-hosts`, `address=/test/127.0.0.1`, and a `pid-file=` in the user's run folder.
- One drop-in file for `systemd-resolved`: `/etc/systemd/resolved.conf.d/pipanel.conf`, followed by `systemctl restart systemd-resolved`.

  ```
  [Resolve]
  DNS=127.0.0.1:5354
  Domains=~test
  ```
- Must be checked on the first run: ordinary domains (`resolvectl query example.com`) must still go through the network DNS and must not get slower because of this global server.
- Fallback when `systemd-resolved` is not used: per-site `/etc/hosts` entries (root needed for every site), or the NetworkManager dnsmasq plugin.

### 4.5 HTTPS
- `mkcert` creates a certificate per site in `~/.config/pipanel/certs/`.
- Plain `mkcert -install` calls `sudo` in a terminal for the system store, which fails from a GUI. So it is split:
  1. As the user: `TRUST_STORES=nss mkcert -install` (creates the local CA and installs it into Firefox/Chrome through NSS, no root).
  2. Through the root helper: copy `$(mkcert -CAROOT)/rootCA.pem` to `/usr/local/share/ca-certificates/pipanel-mkcert.crt` and run `update-ca-certificates` (so `curl`, PHP, and Composer trust it too).
- Nginx uses that certificate in the `listen 443 ssl` block.

### 4.6 User-owned database (MySQL 8 or MariaDB)
One code path serves both engines (`core/mysql.py`, `ENGINES`); the unit is always `pipanel-mysql.service`, and it runs whichever engine is selected. Differences handled per engine:

| | MySQL 8 | MariaDB 10.11 |
|---|---|---|
| Packages | `mysql-server-core-8.0`, `mysql-client-core-8.0` | `mariadb-server-core`, `mariadb-client-core` |
| Server / client | `mysqld` / `mysql` | `mariadbd` / `mariadb` |
| Initialization | `mysqld --initialize-insecure` | `mariadb-install-db --auth-root-authentication-method=normal --skip-test-db` |
| Config options | `mysqlx=OFF`, `lc-messages-dir`, `secure-file-priv=NULL` | none of these (MariaDB refuses unknown options and rejects the value `NULL`) |
| AppArmor profile | `usr.sbin.mysqld` | `usr.sbin.mariadbd` |

- Findings from real testing (MySQL): `secure-file-priv=NULL` is required (without the full `mysql-server` package `/var/lib/mysql-files` does not exist and mysqld refuses to start), and **do not** use `skip-name-resolve` (`@localhost` accounts would stop matching the TCP 127.0.0.1 connections that Laravel uses).
- Finding from real testing (MariaDB): its installer also creates `root@127.0.0.1`, `root@::1`, and `root@<hostname>` with an **empty password**, and `ALTER USER` only changes `root@localhost`. PIPanel drops those extra root accounts after the first start and whenever the root password is changed.
- Install only the core packages. If the full server package is already present, **disable the system service** (`mysql` / `mariadb`).
- Data lives in `~/.local/share/pipanel/<engine>/`, config in `~/.config/pipanel/<engine>/`, the socket and PID in `$XDG_RUNTIME_DIR/pipanel/`. The root password is kept in `client.cnf` (mode 0600) and passed with `--defaults-extra-file`, never on a command line.
- **Mind AppArmor**: the server profile (installed with the full server package) confines the binary to certain paths, so a datadir in the home folder would be denied. During install, if the profile exists, rules for the user's datadir, socket, PID, and log are added in `/etc/apparmor.d/local/<profile>` and the profile is reloaded. If that is a problem, the fallback is the system service.

### 4.7 Node (fnm)
Download the `fnm` binary from the GitHub release into `~/.local/share/pipanel/bin/`. Add the following block to `~/.bashrc` and `~/.zshrc`, with marker comments so it can be removed cleanly:

```sh
# >>> pipanel >>>
export PATH="$HOME/.local/share/pipanel/bin:$PATH"
eval "$(fnm env --use-on-cd --shell bash)"   # --shell zsh in .zshrc
# <<< pipanel <<<
```

The `PATH` line is also added to `~/.profile` so GUI apps (VS Code from the menu) find `php`, `composer`, and `node` after the next login.

### 4.8 ngrok
- Install the official ngrok binary into `~/.local/share/pipanel/bin/`.
- Authtoken: `ngrok config add-authtoken ...`.
- Run: `ngrok http 80 --host-header=name.test`. It always targets the HTTP port, even when the site uses local HTTPS, because ngrok already provides public HTTPS and does not know the mkcert certificate.
- The public URL is read from the local API `http://127.0.0.1:4040/api/tunnels` (the port can move to 4041 and so on when another agent runs; read the ngrok log if needed).
- Laravel note: requests arrive through a proxy, so `TrustProxies` must trust all proxies for generated URLs to use `https`.
- Free-account note: session/endpoint limits and ngrok's warning page the first time a link is opened.

### 4.9 One-time setup with root privileges
A script that runs as root must **not** live in a user-owned folder (anyone who can write to that file could inject root commands). The flow has two steps:

1. **`./install.sh`** (run deliberately in a terminal, `sudo` once) installs the helper `/usr/lib/pipanel/pipanel-helper` (owned by root, mode 755) and the polkit policy `/usr/share/polkit-1/actions/com.pipanel.helper.policy`. It also creates the venv, the launcher, and the icon.
2. In the app, the **"Run setup"** button calls `pkexec pipanel-helper setup` (graphical password dialog). The target user is taken from `PKEXEC_UID`, never from arguments.

The helper only accepts fixed subcommands and validates the arguments: `setup`, `install-php 8.x`, `remove-php 8.x`, `install-ext 8.x name`, `install-database mysql|mariadb`, `trust-ca <path>` (only the user's `~/.local/share/mkcert/rootCA.pem`, which must be a single certificate without a private key), `untrust-ca`, `disable-system-services`.

What `setup` does:
1. Add the `ondrej/php` PPA and `apt install` the base packages (nginx, dnsmasq-base, the default PHP + basic extensions, mkcert, libnss3-tools; MySQL is not included).
2. Disable the system services nginx, mysql, php-fpm.
3. Write the port-80 `sysctl` and the `systemd-resolved` drop-in.
4. Then, from the app as the user: `TRUST_STORES=nss mkcert -install`, the `trust-ca` helper command, the `php` wrapper, Composer, shell integration, write the units, and start the services.

The database is installed separately (`install-database mysql|mariadb`) when needed, including the AppArmor rules when the engine's profile exists.

Polkit note: for scripts, `pkexec` matches the interpreter (`/usr/bin/python3`) and the first argument. The policy contains both. If the match fails, `pkexec` still works with a generic dialog.

## 5. Repository layout

```
PIPanel/
├─ PLAN.md  README.md  pyproject.toml
├─ install.sh  uninstall.sh
├─ packaging/
│  ├─ pipanel.desktop
│  ├─ pipanel-helper            # root helper (Python stdlib only)
│  └─ com.pipanel.helper.policy
├─ pipanel/
│  ├─ __main__.py  app.py        # QApplication, tray, main window
│  ├─ core/                      # logic without UI (testable without Qt)
│  │  ├─ paths.py config.py runner.py
│  │  ├─ sites.py templates.py services.py   # site scan, config rendering, systemd units + systemctl --user
│  │  ├─ php.py node.py mysql.py laravel.py ngrok.py certs.py dns.py
│  │  ├─ privileged.py doctor.py logs.py autostart.py projects.py actions.py
│  │  └─ controller.py           # glue between all modules; the only entry point for the UI
│  ├─ templates/                 # nginx, site, php-fpm, dnsmasq, resolved, mysql, systemd units (Jinja2)
│  ├─ ui/
│  │  ├─ main_window.py theme.py style.qss.tmpl icons.py widgets.py tasks.py
│  │  └─ pages/                  # sites, laravel, php, node, database (mysql.py), share, services, logs, settings
│  └─ assets/                    # pipanel.svg, fonts/ (Inter Regular + SemiBold, OFL license)
├─ tools/                        # screenshots.py and the real end-to-end scripts (e2e_*.py)
├─ docs/                         # documentation site (index.html, assets/) for GitHub Pages
│  └─ assets/img/                # light-*.png and dark-*.png for every page
└─ tests/                        # pytest: core, helper (dry run), offscreen UI
```

## 6. File locations on the user's machine

| Contents | Location |
|---|---|
| App config | `~/.config/pipanel/config.json` |
| Rendered nginx / php / dnsmasq config | `~/.config/pipanel/{nginx,php,dns}/` |
| systemd user units | `~/.config/systemd/user/pipanel-*.service` |
| Certificates | `~/.config/pipanel/certs/` |
| Sockets and PIDs | `$XDG_RUNTIME_DIR/pipanel/` (tmpfs, cleaned automatically at logout, so no stale PIDs) |
| Logs | `~/.local/state/pipanel/logs/` |
| MySQL data | `~/.local/share/pipanel/mysql/` |
| Extra binaries (php wrapper, composer, fnm, ngrok) | `~/.local/share/pipanel/bin/` |
| Root helper (installed by `install.sh`) | `/usr/lib/pipanel/pipanel-helper` |

## 7. Site template (Laravel example)

```nginx
server {
    listen {{ http_port }};
    {% if https %}
    listen {{ https_port }} ssl;
    http2 on;                             # nginx >= 1.25.1; older: "listen 443 ssl http2;"
    ssl_certificate     {{ cert }};
    ssl_certificate_key {{ key }};
    {% endif %}

    server_name {{ name }}.test;
    root {{ docroot }};
    index index.php index.html;
    charset utf-8;
    client_max_body_size {{ upload_max }};  # same as upload_max_filesize

    access_log {{ log_dir }}/{{ name }}-access.log;
    error_log  {{ log_dir }}/{{ name }}-error.log;

    location / {
        try_files $uri $uri/ /index.php?$query_string;
    }

    location ~ \.php$ {
        try_files $uri =404;
        include {{ etc_nginx }}/fastcgi_params;   # absolute path, see 4.3
        fastcgi_param SCRIPT_FILENAME $realpath_root$fastcgi_script_name;
        fastcgi_param DOCUMENT_ROOT   $realpath_root;
        fastcgi_pass unix:{{ php_socket }};   # socket of the site's PHP version
    }

    location ~ /\.(?!well-known).* { deny all; }
}
```

Changing a site's PHP version = change `php_socket`, run `nginx -t`, then reload nginx. Changing its web root = change `root`. Ubuntu 22.04 ships nginx 1.18, so `templates.py` picks the `http2` syntax according to `nginx -v`.

## 8. Work stages

### Stage 1: Core (Sites + PHP + DNS + HTTPS)
- [x] Project skeleton, `paths`, `config`
- [x] nginx / php-fpm / dnsmasq / systemd unit templates + render tests
- [x] `sites.py`: scan projects folders, detect the docroot (with a per-site override), tests
- [x] `services.py`: write units, start/stop/status nginx, php-fpm, dnsmasq via `systemctl --user`
- [x] Root helper + polkit policy (`pkexec`) for packages, sysctl, resolved (**not yet tested on Mint**)
- [x] `certs.py` + HTTPS toggle (including `trust-ca`)
- [x] `php.py`: user conf.d, CLI wrapper, Composer
- [x] Theme: `ui/theme.py`, QSS template, Light / Dark / Follow system toggle (section 13)
- [x] UI: Sites and Services pages
- [x] Install/remove PHP versions, switch per site

**Done when:** a folder in `~/PHPProject` opens automatically at `https://name.test` with the PHP version of your choice, without Docker and without daily `sudo`, and the site keeps working after the app window is closed.

### Stage 2: Node + MySQL
- [x] fnm: install/switch versions, shell integration
- [x] User-owned MySQL instance + AppArmor rules
- [x] Create/drop databases and users, change the root password
- [x] `php.ini` editor and extension toggles
- [x] Create a new Laravel project from the app

**Done when:** a Laravel project can `migrate` into PIPanel's MySQL and the Node version can be switched from the app.

### Stage 3: Extras
- [x] ngrok: authtoken, share, public URL
- [x] System tray + open at login
- [x] Log viewer
- [x] Doctor (diagnostics)
- [ ] Distribution package (`.deb` or AppImage), optional

### Stage 4: Daily comfort
- [x] `php`/`composer` in a terminal follow the site's PHP version: the wrapper reads `~/.config/pipanel/php/sites.map` (deepest folder first); `PIPANEL_PHP=8.x` overrides
- [x] PHP-FPM on demand: only versions used by a site, the default, or a PIPanel tool run; `apply()` starts and stops the others
- [x] Mailpit (`mailpit.test`), Redis (root helper `install-redis`, helper version 2), Adminer and phpMyAdmin (`adminer.test`, `phpmyadmin.test`): local tools answer 127.0.0.1 only (nginx `allow`/`deny`) and sign in with the root password read from `client.cnf` on every request
- [x] Proxy sites (`name.test` -> another dev server), with WebSocket upgrades; the upstream sees its own `Host` (Vite rejects unknown hosts), the original goes in `X-Forwarded-Host`
- [x] Site processes (Vite through `fnm exec`, `queue:listen`, `schedule:work`, Reverb, Horizon) as `pipanel-site-<site>-<process>` units; never enabled at login
- [x] Xdebug toggle per version (off by default) and a VS Code `launch.json`
- [x] Database table with backup (`.sql.gz`), import, clone, "use in project"; users with their access; pages of 20 for long lists
- [x] Set up for projects cloned from Git: composer install, `.env`, `APP_KEY`, database, migrate or import a dump
- [x] New folders are served by themselves: `pipanel-watch.path` (inotify) runs `pipanel sync --settle`, which keeps rescanning while a fresh folder is still filling
- [x] `.env` sync: `APP_URL` follows HTTPS and the ports when it pointed at the site
- [x] Cloudflare quick tunnel as a second share provider (no account); public host from cloudflared's `/quicktunnel` metrics endpoint
- [x] WordPress through WP-CLI (passwords through stdin)
- [x] Tray: services with start/stop, PHP versions with their sites (a count above two), default PHP, recent sites; sidebar status; Ctrl+K search
- [x] Notifications (`notify-send` with Restart/Details) when a service crashes (`failed`, or `active` -> `activating`), not when the user stops it
- [x] Tools the user stops (database, Mailpit, Redis) are remembered in `stopped_tools` and stay off with Start all and at login
- [x] `pipanel` command for terminals (status, sites, which, link, isolate, use, secure, proxy, share, sync, doctor, ...)
- [x] Downloads resume with HTTP Range when the connection drops (large files on slow lines)
- [ ] PostgreSQL
- [ ] Distribution package

**Done when:** a project cloned into the projects folder opens at `name.test` without touching the app, one click makes it run, and a crash says why.

### Stage 5: Windows, macOS, packages
- [x] `core/platform.py`: one code base; `PIPANEL_PLATFORM` forces a platform in tests
- [x] `core/supervisor.py`: services are specs (`argv`, `env`, `workdir`, `dirs`, `reload`); `SystemdSupervisor` writes user units (Linux), `AgentSupervisor` hands them to the agent (Windows, macOS)
- [x] `core/agent.py` (`pipanel _agent`): starts, stops, reloads, restarts on failure (5 in 30 s marks a service failed), logs to `logs/units/<name>.log`, starts enabled services at login (Run key / LaunchAgent), adopts services of a previous agent, watches the projects folders; requests and answers are files, no socket
- [x] `core/dnsd.py` (`pipanel _dns`): `*.test` -> 127.0.0.1 for Windows (NRPT rule, port 53: the Windows client cannot use another port) and macOS (`/etc/resolver/test`, port 5354)
- [x] Windows PHP: official windows.php.net builds (checksum from `releases.json`), a pool of `php-cgi.exe` per version on TCP ports (`core/fcgipool.py`, nginx `upstream`), extensions as DLLs enabled through PIPanel's conf.d; Xdebug from xdebug.org and other extensions from PECL's Windows builds
- [x] macOS PHP: Homebrew kegs from `shivammathur/php` (`php@8.x`), PHP-FPM like Linux; extensions from `shivammathur/extensions`
- [x] `core/stack.py`: where Nginx, the databases, Redis and mkcert come from (apt / downloads / Homebrew); Windows downloads nginx.org's stable zip, MariaDB 11.4 LTS (REST API, SHA-256) or MySQL 8.4 LTS, Redis from redis-windows
- [x] One administrator step on Windows (`packaging/windows/pipanel-helper.ps1` in Program Files, UAC) and macOS (password dialog): the DNS rule and trusting mkcert's CA, in a single prompt
- [x] Paths: `%LOCALAPPDATA%\PIPanel`, `~/Library/PIPanel` (no spaces: socket paths go into nginx and PHP-FPM configs); nginx paths quoted and with forward slashes; Nginx listens on 127.0.0.1 on Windows and macOS (no firewall prompt)
- [x] `php`/`composer`/`wp` on Windows: `.cmd` files calling `pipanel _run`, which picks the site's PHP version like the shell wrapper
- [x] Packages: `.deb` per Ubuntu release with a trimmed Qt (`packaging/build-deb.sh`, `prune_qt.py`, ldd-checked), PyInstaller app (Windows: windowed `PIPanel.exe` + console `pipanel-cli.exe`; macOS: `PIPanel.app`), Inno Setup installer, `.dmg`
- [x] GitHub Actions: unit tests on Linux (3.10, 3.12), Windows, macOS; real services on Windows and macOS (`tools/e2e_desktop.py`); a `v*` tag builds, installs/starts and publishes every package
- [ ] Code signing (Windows, macOS notarization)
- [ ] PostgreSQL

**Done when:** the same app installs from one package on Mint, Windows and macOS, and a site opens at `name.test` after Run setup.

## 9. Testing

- **Automated (221 tests):** pure logic; the root helper in dry-run mode; real dnsmasq answering raw DNS queries; real mkcert creating certificates; the offscreen UI (every page, theme, HTTPS toggle, PHP switch, web root, projects folder, database engine switch, Laravel packages, tray). A test hook turns exceptions that Qt would swallow inside callbacks into failures.
- **Real tests (no root), run as scripts in `tools/`:**
  - `e2e_nginx_php.py`: Nginx 1.24 + PHP-FPM 8.3 + dnsmasq as `systemctl --user` units; HTTP, HTTPS with the mkcert CA, Laravel routing, an unknown host, dotfile blocking, per-site logs, and reload after a change.
  - `e2e_database.py <dir> mysql|mariadb`: initialization, start as a unit, database/user management, root password change, extra-root cleanup (MariaDB), and TCP login.
  - `e2e_laravel_packages.py`: real `composer require`/`remove` through a site's PHP version and conf.d.
  - `e2e_downloads.py`: Composer, fnm + Node, and ngrok downloads.
- **Unit tests (pytest)** for pure logic: template rendering, site scanning, docroot detection, reading/writing config, conf.d contents, and the PHP wrapper. They run without Qt and without Nginx. Every path comes from `paths.py` so tests can use a temporary folder (`tmp_path`).
- **Manual tests on Mint** (cannot be fully automated):
  - `resolvectl query name.test` and `dig name.test` return 127.0.0.1
  - `resolvectl query example.com` still works normally (other DNS is not disturbed)
  - `curl -I https://name.test` succeeds without a certificate warning, and so do Firefox/Chrome
  - Switch a site to PHP 8.2 and `phpinfo()` shows 8.2
  - `php -v` in a new terminal follows the chosen CLI version
  - `mysql` connects through the socket and through `127.0.0.1:3306`
  - With the app window closed the site still opens. After logout/login the services recover according to the autostart setting
- Offscreen UI screenshots: `python tools/screenshots.py` (results in `docs/assets/img/`).

## 10. Risks and things to watch

| Risk | Impact | Mitigation |
|---|---|---|
| MariaDB's installer leaves extra `root` accounts with an empty password | Anyone on the machine could log in as root | Dropped after the first start and on every root password change; covered by a real test |
| A config option one engine accepts breaks the other (`mysqlx`, `secure-file-priv=NULL`) | The server fails to start | Per-engine template blocks, found and verified with real runs |
| AppArmor blocks `mysqld` in the home folder | MySQL fails to start | Local AppArmor rules during setup; fallback: the system MySQL service |
| `.test` does not resolve (Mint without `systemd-resolved` active) | Domains do not work | Doctor detects it, fallback through `/etc/hosts` |
| The global DNS in resolved also receives non-`.test` queries | Other domains slow/fail | Checked in Stage 1; dnsmasq without upstream (`no-resolv`) only answers `.test` |
| Port 5353 clashes with Avahi | dnsmasq fails to start | Use port 5354 (changeable in config) |
| Port 80/443 used by Apache or the system nginx | Nginx fails to start | Setup stops the system services, Doctor shows who uses the port |
| Updating/installing PHP re-enables system services | Duplicate processes, port clashes | The helper disables them after every install; Doctor checks and offers a fix |
| mkcert disappears if it is removed along with something else (e.g. when DDEV is removed) | HTTPS breaks | The setup script makes sure mkcert is installed on its own |
| A root script can be modified by the user/another process | Privilege escalation | The helper is installed in root-owned `/usr/lib/pipanel/`, arguments are validated |
| Qt cannot detect the system theme on Cinnamon | "Follow system" does not change | Fallback: read Cinnamon's `gsettings` (polled every 4 seconds) |
| The tray is not shown on some desktops | No tray icon | The app is still usable from the main window |
| A user-chosen web root points outside the project | Files outside the project get served | The web root is resolved and must stay inside the project folder (symlinks that escape are rejected) |
| Cannot be fully tested without root | Bugs in the real environment | Work in stages, manual test on Mint after every stage |
| Free ngrok accounts are limited | Limited sharing | Show the limits in the UI; another tunnel (Cloudflare) can be added later |

## 11. Prerequisites on the development machine

```
sudo apt install -y python3-venv python3-pip git curl libxcb-cursor0
python3 -m venv .venv && source .venv/bin/activate
pip install PySide6 pytest
```

`libxcb-cursor0` is required for Qt 6.5 and newer; without it PySide6 fails with "Could not load the Qt platform plugin xcb".

## 12. Next steps (test on Mint)

1. `sudo apt install -y python3-venv python3-pip libxcb-cursor0`, then `./install.sh`.
2. Open PIPanel, click **Run setup**, and report the result of every step (the result dialog shows errors as they are).
3. Choose your projects folder (for example `~/PHPProject`) and go through the manual test list in section 9. The things most likely to need fixes: the `systemd-resolved` drop-in, `pkexec`, and MySQL AppArmor.
4. Fix as you go; every fix gets a test.

---

## 13. Design and theme

Style: **modern, simple, airy**. Plenty of whitespace, few lines, one main color (purple), and other colors only for status. Herd-like feel: clean, not busy.

### 13.1 Color palette

Purple is the main color, combined with **teal** as the accent and **indigo** as the info color. The neutral grays get a slight purple tint so everything blends together.

| Token | Light | Dark | Used for |
|---|---|---|---|
| `bg` | `#FAF9FC` | `#14111F` | Window background |
| `surface` | `#FFFFFF` | `#1C1830` | Cards, panels |
| `surface-2` | `#F3F0FA` | `#251F3D` | Sidebar, hover rows |
| `border` | `#E7E3F0` | `#2E2848` | Thin lines |
| `text` | `#1F1B2E` | `#EDEAF7` | Main text |
| `text-muted` | `#6B6580` | `#9A93B5` | Supporting text |
| `primary` | `#7C3AED` | `#7C3AED` | Primary button, active item |
| `primary-hover` | `#6D28D9` | `#8B5CF6` | Primary button hover |
| `primary-text` | `#7C3AED` | `#A78BFA` | Links and purple text (lighter in dark so it stays readable) |
| `primary-soft` | `#EDE9FE` | `#2A2250` | Active item background, badges |
| `on-primary` | `#FFFFFF` | `#FFFFFF` | Text on a purple button |
| `accent` | `#14B8A6` | `#2DD4BF` | Second accent (teal), used sparingly |
| `success` | `#14B8A6` | `#2DD4BF` | Dots/icons for "running" |
| `success-text` | `#0F766E` | `#2DD4BF` | Text/labels for "running" |
| `warning` | `#F59E0B` | `#FBBF24` | Warning dots/icons |
| `warning-text` | `#B45309` | `#FBBF24` | Warning text |
| `danger` | `#F43F5E` | `#FB7185` | Error dots/icons |
| `danger-text` | `#BE123C` | `#FB7185` | Error text |
| `danger-button` | `#BE123C` | `#BE123C` | Delete button background (white text) |
| `info` | `#6366F1` | `#818CF8` | Info dots/icons (indigo) |
| `info-text` | `#4F46E5` | `#818CF8` | Info text |

Usage rules:
- Purple is used for: the primary button, the active sidebar item, links, input focus, and active toggles.
- Teal is only for the "running" status and small accents. Do not combine it with purple in one button.
- The primary button uses `#7C3AED` in both themes with white text (contrast 5.7:1, meets WCAG AA). The delete button uses `#BE123C` with white text (6.3:1).
- The bright status colors (`success`, `warning`, `danger`, `info`) only reach 2–4.5:1 against a white background in the light theme, so they are **for dots, icons, and lines only**. Status text uses the `*-text` tokens (at least 4.5:1 against `bg` and `surface`).
- All values live in **one place** (`ui/theme.py`), so changing a color only means changing that table.

### 13.2 Typography and shape
- Font: **Inter** Regular and SemiBold as static files (bundled in `assets/fonts/`, OFL license, loaded with `QFontDatabase`), with the system font as a fallback. The variable version is not used because its semibold weight is not picked up by Qt.
- Sizes: page title 20 px semibold, card title 14 px semibold, body 13 px, captions 12 px.
- Rounded corners: cards 12 px, buttons and inputs 8 px, badges fully rounded (pills).
- Spacing uses multiples of 8 px. Shadows are very light or absent; separators use `border` lines.
- Icons: a thin-line SVG set in the Lucide style (ISC license), colored to follow the theme.

### 13.3 Layout
- **Left sidebar** (about 220 px wide): a small logo, then the menu Sites, Laravel, PHP, Node, Database, Share, Services, Logs, Settings. The active item uses `primary-soft` with `primary-text`.
- **Page header**: the title on the left, the main action buttons on the right, and the theme button (sun/moon icon) in the top-right corner.
- **Content**: cards on the `bg` background; the site list is made of roomy rows with name, path and web root, a project-type badge, a PHP version picker, an HTTPS toggle, and icon action buttons (always visible and reachable by keyboard).
- **Services**: cards with a status dot (teal = running, gray = stopped, rose = error) plus a text label, the name, and an on/off toggle. Status is never distinguished by color alone.
- **Empty state**: a large icon with one sentence and one button ("Choose projects folder").
- The window can shrink to about 900 x 600 without clipped elements.

### 13.4 Theme choice
- Three options in Settings and through the header button: **Light**, **Dark**, **Follow system** (default).
- The choice is stored in `config.json` (`"theme": "system" | "light" | "dark"`).
- "Follow system" reads `QGuiApplication.styleHints().colorScheme()` and listens to the `colorSchemeChanged` signal (Qt 6.5 or newer). If the result is `Unknown` on Cinnamon, the fallback reads `gsettings` (`org.cinnamon.desktop.interface`, then `org.gnome.desktop.interface`; `color-scheme` or a `gtk-theme` name that contains "dark") and re-checks it every 4 seconds.
- Switching the theme takes effect immediately without restarting the app.

### 13.5 Implementation notes (PySide6)
- Qt QSS does not support a bare `[attribute]` selector; write `[badge="primary"]` for each value.
- `border-radius` on a QLabel is only painted when the radius does not exceed half of the widget height (badges: radius 9 px, padding 2 px).
- A removed widget must be detached first (`hide()` + `setParent(None)`) before `deleteLater()`, otherwise the old image stays visible until the next event-loop pass.

Basic steps:
1. Use the **Fusion** style as the base so the look is the same on every desktop.
2. `theme.py` holds two token dictionaries (light and dark) and an `apply_theme(app, mode)` function.
3. `style.qss.tmpl` holds the QSS with placeholders such as `{{primary}}`; when the theme changes, the template is rendered again and applied with `app.setStyleSheet(...)`.
4. `QPalette` is filled from the same tokens so built-in dialogs (folder picker, messages) do not show different colors.
5. SVG icons are colored by replacing `currentColor` before `QSvgRenderer` draws them.
6. The on/off toggle is a small custom widget (not the built-in checkbox) so it looks modern, and it stays focusable and toggled with Space.

### 13.6 Theme completion criteria
- [x] Both themes look tidy on every page (checked through screenshots).
- [x] Text contrast is at least 4.5:1 in light and dark (checked automatically by `tests/test_ui.py`).
- [x] Switching the theme takes effect immediately, and the choice persists after the app is reopened.
- [ ] "Follow system" changes when the desktop theme changes (the logic is tested; **not yet tried on a real Cinnamon session**).
- [x] Offscreen screenshots (light and dark) for every page are saved in `docs/assets/img/` for review.
