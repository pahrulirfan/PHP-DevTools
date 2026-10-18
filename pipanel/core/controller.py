"""Glue between all core modules. The UI only calls this class."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

from . import config as cfgmod
from . import ports
from . import autostart, processes, projects, watch
from .adminer import SITE as ADMINER_SITE, Adminer
from .certs import Certs, CertError
from .cloudflare import Cloudflared
from .config import Config, SiteOverride
from .doctor import Check, Doctor
from .laravel import LaravelTools
from .mailpit import SITE as MAILPIT_SITE, UI_PORT as MAILPIT_PORT, Mailpit
from .mysql import ENGINES, MySQL
from .phpmyadmin import SITE as PMA_SITE, PhpMyAdmin, pick_php
from .ngrok import Ngrok, TokenCheck
from .ngrok import fingerprint as ngrok_fingerprint
from .node import Node, ShellIntegration
from .paths import Paths
from .php import PHP, SUPPORTED, http_fetch
from .privileged import Privileged
from .redis import Redis
from .runner import Result, Runner
from .services import (ADDONS, CLOUDFLARED, DNS, MAIL, MYSQL, NGINX, NGROK, REDIS, TUNNELS, Services, SyncReport,
                       path_unit_name, php_service, service_title, site_process_service, split_site_process,
                       unit_name)
from .sites import LARAVEL, Proxy, Site, normalize_target, relative_docroot, safe_subdir, scan, slugify
from .templates import valid_site_name
from .wordpress import NewSite, WordPress, WordPressError


class ControllerError(Exception):
    pass


class Controller:
    def __init__(self, paths: Paths | None = None, runner: Runner | None = None, *,
                 fetch: Callable[[str], bytes] = http_fetch, home: Path | None = None,
                 php: PHP | None = None, privileged: Privileged | None = None):
        self.paths = paths or Paths.from_env()
        self.runner = runner or Runner()
        self.fetch = fetch
        self.home = home or Path(os.environ.get("HOME", str(Path.home())))
        self.cfg: Config = cfgmod.load(self.paths.config_file)
        self.php = php or PHP(self.paths)
        self.services = Services(self.paths, self.runner, self.php)
        self.certs = Certs(self.paths, self.runner)
        self.priv = privileged or Privileged(self.runner)
        self.node = Node(self.paths, self.runner)
        self.shell = ShellIntegration(self.paths, self.home, self.node)
        self.mysql = MySQL(self.paths, self.runner, port=self.cfg.mysql_port, engine=self.cfg.db_engine)
        self.config_home = Path(os.environ.get("XDG_CONFIG_HOME") or self.home / ".config")
        self.ngrok = Ngrok(self.paths, self.runner, home=self.home, config_home=self.config_home)
        self.laravel = LaravelTools(self.paths, self.runner, self.php)
        self.mailpit = Mailpit(self.paths)
        self.redis = Redis(self.paths)
        self.adminer = Adminer(self.paths)
        self.phpmyadmin = PhpMyAdmin(self.paths)
        self.cloudflared = Cloudflared(self.paths)
        self.wordpress = WordPress(self.paths, self.runner, self.php)
        self.last_notes: list[str] = []

    # ---------- config ----------
    def save(self) -> None:
        cfgmod.save(self.cfg, self.paths.config_file)

    def sites(self) -> list[Site]:
        return scan(self.cfg)

    def site(self, name: str) -> Site:
        for s in self.sites():
            if s.name == name:
                return s
        raise ControllerError(f"site not found: {name}")

    def proxies(self) -> list[Proxy]:
        """Proxy sites added by the user (name.test -> another local dev server)."""
        return [Proxy(n, t, self.cfg.site(n).https) for n, t in sorted(self.cfg.proxies.items())
                if valid_site_name(n) and normalize_target(t) == t]

    def _internal(self, sites: list[Site]) -> tuple[list[Site], list[Proxy]]:
        """PIPanel's own tools that get a *.test name: Mailpit (proxy) and Adminer (PHP). A project with the same
        name wins."""
        taken = {s.name for s in sites} | set(self.cfg.proxies)
        isites, iproxies = [], []
        if self.mailpit.installed() and MAILPIT_SITE not in taken:
            iproxies.append(Proxy(MAILPIT_SITE, f"http://127.0.0.1:{MAILPIT_PORT}", local_only=True, internal=True))
        if self.adminer.installed() and ADMINER_SITE not in taken:
            root = self.adminer.root
            isites.append(Site(ADMINER_SITE, root, root, "php", self.cfg.default_php, False))
        if self.phpmyadmin.installed() and PMA_SITE not in taken:
            root = self.phpmyadmin.root
            isites.append(Site(PMA_SITE, root, root, "php", pick_php(self.php.installed(), self.cfg.default_php),
                               False))
        return isites, iproxies

    def _write_db_tools(self) -> None:
        """Point Adminer and phpMyAdmin at the current engine's root password and port."""
        if self.adminer.installed():
            self.adminer.write_index(self.mysql.client_cnf, self.cfg.mysql_port)
        if self.phpmyadmin.installed():
            self.phpmyadmin.write_config(self.mysql.client_cnf, self.cfg.mysql_port)

    def db_tools(self) -> dict[str, str]:
        """Installed web database managers: title -> URL."""
        out = {}
        if self.phpmyadmin.installed():
            out["phpMyAdmin"] = self.host_url(PMA_SITE)
        if self.adminer.installed():
            out["Adminer"] = self.host_url(ADMINER_SITE)
        return out

    def db_tool_url(self, tool: str, database: str) -> str:
        """Open one database directly in a tool."""
        from urllib.parse import quote
        if tool == "phpMyAdmin":
            return f"{self.host_url(PMA_SITE)}/index.php?route=/database/structure&db={quote(database)}"
        return f"{self.host_url(ADMINER_SITE)}/?server=127.0.0.1:{self.cfg.mysql_port}&username=root&db={quote(database)}"

    def apply(self) -> SyncReport:
        """Rewrite the config from current state, then reload the running services and start or stop PHP-FPM
        versions so that exactly the versions in use run."""
        sites = self.sites()
        isites, iproxies = self._internal(sites)
        self._write_db_tools()
        rep = self.services.sync(self.cfg, sites, self.proxies() + iproxies, isites)
        self.php.write_site_map([(s.path, s.php) for s in sites])
        cli = self.php.cli_version()
        if cli:
            self.php.write_cli_wrapper(cli)   # brings a wrapper written by an older version up to date
        if rep.changed:
            names = [NGINX] + [php_service(v) for v in self.php.installed()]
            active = [n for n, s in self.services.status(names).items() if s == "active"]
            for n in active:
                self.services.reload(n)
        self._reconcile_php(sites + isites)
        self._write_watcher()
        return rep

    def _write_watcher(self) -> None:
        """Watch the projects folders so that a new folder is served right away, even with the app closed."""
        f = self.paths.systemd_user / path_unit_name(watch.SERVICE)
        folders = [p for p in self.cfg.parked if Path(p).is_dir()]
        if not folders:
            if f.exists():
                self.runner.run(["systemctl", "--user", "disable", "--now", f.name])
                f.unlink()
                self.services.remove_unit(watch.SERVICE)
            return
        self.services.write_extra_unit(
            watch.SERVICE, description="PIPanel: serve new project folders",
            exec_start=f"{sys.executable} -m pipanel sync --settle",
            env=watch.sync_env(self.paths.config, self.paths.data, self.paths.state, self.paths.run))
        text = watch.render_path_unit(folders)
        if f.exists() and f.read_text() == text:
            return
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        self.runner.run(["systemctl", "--user", "daemon-reload"])
        self.runner.run(["systemctl", "--user", "enable" if self.cfg.autostart_services else "disable", f.name])
        self.runner.run(["systemctl", "--user", "restart", f.name])   # watch the new list of folders now

    def sync(self, settle: bool = False) -> int:
        """Bring the config in line with the folders on disk (run by the folder watcher)."""
        if not settle:
            self.apply()
            return 1
        return watch.settle(self.sites, self.apply)

    def php_in_use(self, sites: list[Site] | None = None) -> list[str]:
        """Installed PHP versions that a site (or the default) uses. Only these need a running PHP-FPM."""
        if sites is None:
            sites = self.sites()
            sites = sites + self._internal(sites)[0]   # Adminer, phpMyAdmin
        used = {s.php for s in sites} | {self.cfg.default_php}
        return [v for v in self.php.installed() if v in used]

    def _reconcile_php(self, sites: list[Site]) -> None:
        """While the web server runs, start the PHP-FPM versions that sites need and stop the ones no site uses."""
        installed = self.php.installed()
        if not installed:
            return
        want = set(self.php_in_use(sites))
        st = self.services.status([NGINX] + [php_service(v) for v in installed])
        web_up = st.get(NGINX) == "active"
        for v in installed:
            n = php_service(v)
            running = st.get(n) in ("active", "activating")
            if v in want and web_up and not running:
                self.services.start(n)
                if self.cfg.autostart_services:
                    self.services.enable(n)
            elif v not in want and running:
                self.services.stop(n)
                self.services.disable(n)

    # ---------- sites ----------
    def park(self, folder: str | Path) -> None:
        p = str(Path(folder).expanduser())
        if not Path(p).is_dir():
            raise ControllerError(f"not a folder: {p}")
        if p not in self.cfg.parked:
            self.cfg.parked.append(p)
        self.save()

    def replace_parked(self, old: str, new: str | Path) -> None:
        """Point an existing projects folder somewhere else (keeps its position in the list)."""
        new_s = str(Path(new).expanduser())
        if not Path(new_s).is_dir():
            raise ControllerError(f"not a folder: {new_s}")
        if old in self.cfg.parked:
            self.cfg.parked = [new_s if p == old else p for p in self.cfg.parked if p != new_s or p == old]
        elif new_s not in self.cfg.parked:
            self.cfg.parked.append(new_s)
        self.save()

    def unpark(self, folder: str) -> None:
        self.cfg.parked = [p for p in self.cfg.parked if p != folder]
        self.save()

    def link(self, name: str, folder: str | Path) -> None:
        name = name.strip().lower()
        if not valid_site_name(name):
            raise ControllerError("the name may only contain lowercase letters, digits, and '-'")
        if not Path(folder).is_dir():
            raise ControllerError(f"not a folder: {folder}")
        self.cfg.linked[name] = str(folder)
        self.save()

    def unlink(self, name: str) -> None:
        self.cfg.linked.pop(name, None)
        self.save()

    def _override(self, name: str) -> SiteOverride:
        return self.cfg.sites.setdefault(name, SiteOverride())

    def set_site_php(self, name: str, version: str | None) -> SyncReport:
        if version is not None and version not in SUPPORTED:
            raise ControllerError(f"unknown PHP version: {version}")
        self._override(name).php = version
        self.save()
        return self.apply()

    def set_site_docroot(self, name: str, rel: str | None) -> SyncReport:
        """Set the web root of a site relative to its project folder; empty/None = auto-detect."""
        site = self.site(name)
        rel = (rel or "").strip()
        if not rel:
            self._override(name).docroot = None
        else:
            chosen = safe_subdir(site.path, rel)
            if chosen is None:
                raise ControllerError(f"'{rel}' is not a folder inside {site.path}")
            probe = Site(site.name, site.path, chosen, site.kind, site.php, site.https)
            self._override(name).docroot = relative_docroot(probe)
        self.save()
        return self.apply()

    def set_https(self, name: str, on: bool) -> SyncReport:
        if on:
            try:
                self.certs.ensure_site_cert(name, self.cfg.tld)
            except CertError as e:
                raise ControllerError(str(e)) from e
        self._override(name).https = on
        self.save()
        rep = self.apply()
        site = next((s for s in self.sites() if s.name == name), None)
        if site and self.sync_app_url(site):
            rep.warnings.append(f"{name}: APP_URL in .env now uses {'https' if on else 'http'}")
        return rep

    def site_url(self, s: Site | Proxy) -> str:
        return self.host_url(s.name, s.https)

    def host_url(self, name: str, https: bool = False) -> str:
        scheme, port, default = ("https", self.cfg.https_port, 443) if https else ("http", self.cfg.http_port, 80)
        return f"{scheme}://{name}.{self.cfg.tld}" + ("" if port == default else f":{port}")

    # ---------- proxy sites ----------
    def add_proxy(self, name: str, target: str) -> SyncReport:
        name = name.strip().lower()
        if not valid_site_name(name):
            raise ControllerError("the name may only contain lowercase letters, digits, and '-'")
        if name in {s.name for s in self.sites()} or name in (MAILPIT_SITE, ADMINER_SITE, PMA_SITE):
            raise ControllerError(f"{name}.{self.cfg.tld} is already used")
        norm = normalize_target(target)
        if norm is None:
            raise ControllerError("Enter a port (3000) or an address such as http://127.0.0.1:5173")
        self.cfg.proxies[name] = norm
        self.save()
        return self.apply()

    def remove_proxy(self, name: str) -> SyncReport:
        self.cfg.proxies.pop(name, None)
        self.save()
        return self.apply()

    # ---------- .env ----------
    def sync_app_url(self, site: Site) -> bool:
        """Point APP_URL at the site's current URL when it still points at this site (other scheme or port) or
        at Laravel's default http://localhost. Any other value was chosen on purpose and is left alone."""
        if site.kind != LARAVEL or not (site.path / ".env").is_file():
            return False
        cur = projects.dotenv_get(site.path, "APP_URL")
        want = self.site_url(site)
        host = re.sub(r"^https?://", "", cur or "").split("/")[0].split(":")[0]
        if cur == want or not (cur in (None, "", "http://localhost") or host == f"{site.name}.{self.cfg.tld}"):
            return False
        projects.set_env(site.path, {"APP_URL": want})
        return True

    def write_env(self, site_name: str, preset: str, database: str | None = None) -> dict[str, str]:
        """Fill a Laravel project's .env with the connection settings of a PIPanel service."""
        site = self.site(site_name)
        if preset == "database":
            values = projects.mysql_env(self.cfg.mysql_port, database or slugify(site.name).replace("-", "_"),
                                        self.mysql.root_password())
        elif preset == "mail":
            values = self.mailpit.env()
        elif preset == "redis":
            values = self.redis.env()
        else:
            raise ControllerError(f"unknown preset: {preset}")
        try:
            projects.set_env(site.path, values)
        except projects.ProjectError as e:
            raise ControllerError(f"{site.name}: {e}") from e
        return values

    # ---------- PHP ----------
    def installed_php(self) -> list[str]:
        return self.php.installed()

    def install_php(self, v: str) -> Result:
        r = self.priv.install_php(v)
        if r.ok:
            self.php.ensure_conf_d(v, self.cfg.php_settings)
            self.apply()
            self.services.start(php_service(v))
        return r

    def remove_php(self, v: str) -> Result:
        if v == self.cfg.default_php:
            raise ControllerError("The default PHP version cannot be removed; change the default first")
        users = [s.name for s in self.sites() if s.php == v]
        if users:
            raise ControllerError(f"still in use by: {', '.join(users)}")
        self.services.stop(php_service(v))
        self.services.disable(php_service(v))
        r = self.priv.remove_php(v)
        if r.ok:
            (self.paths.systemd_user / unit_name(php_service(v))).unlink(missing_ok=True)
            self.runner.run(["systemctl", "--user", "daemon-reload"])
        return r

    def set_default_php(self, v: str) -> SyncReport:
        if v not in self.php.installed():
            raise ControllerError(f"PHP {v} is not installed")
        self.cfg.default_php = v
        self.save()
        self.php.write_cli_wrapper(v)
        return self.apply()

    def set_php_settings(self, settings: dict[str, str]) -> None:
        from .php import validate_setting, PHPError
        try:
            clean = {k: validate_setting(k, v) for k, v in settings.items()}
        except PHPError as e:
            raise ControllerError(str(e)) from e
        self.cfg.php_settings.update(clean)
        self.save()
        self.apply()
        for v in self.php.installed():
            self.services.reload(php_service(v))

    def toggle_extension(self, v: str, name: str, on: bool) -> None:
        self.php.set_extension(v, name, on)
        self.services.restart(php_service(v))

    def set_xdebug(self, v: str, on: bool) -> None:
        if self.php.xdebug_state(v) == "missing":
            raise ControllerError(f"Xdebug is not installed for PHP {v}")
        self.php.set_xdebug(v, on)
        if self.services.status([php_service(v)]).get(php_service(v)) == "active":
            self.services.restart(php_service(v))

    def vscode_launch(self, site_name: str) -> Path:
        """Add a "Listen for Xdebug" configuration to the project's .vscode/launch.json."""
        site = self.site(site_name)
        f = site.path / ".vscode" / "launch.json"
        conf = {"name": "Listen for Xdebug (PIPanel)", "type": "php", "request": "launch", "port": 9003}
        data = {"version": "0.2.0", "configurations": []}
        if f.exists():
            try:
                data = json.loads(f.read_text())
                if not isinstance(data, dict) or not isinstance(data.setdefault("configurations", []), list):
                    raise ValueError
            except ValueError:
                raise ControllerError(f"{f} has comments or is not plain JSON. Add this configuration to it "
                                      f"yourself:\n\n{json.dumps(conf, indent=4)}")
        if not any(isinstance(c, dict) and c.get("type") == "php" and c.get("port") == 9003
                   for c in data["configurations"]):
            data["configurations"].append(conf)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(data, indent=4) + "\n")
        return f

    # ---------- setup & layanan ----------
    def setup(self) -> list[tuple[str, Result]]:
        """First-time setup. Stops at the first root step that fails."""
        steps: list[tuple[str, Result]] = []
        r = self.priv.setup(self.cfg.default_php)
        steps.append(("Packages and system settings", r))
        if not r.ok:
            return steps
        self.paths.ensure()
        steps.append(("mkcert CA (browser)", self.certs.install_ca_user()))
        root = self.certs.caroot()
        if root and (root / "rootCA.pem").exists():
            steps.append(("mkcert CA (system)", self.priv.trust_ca(root / "rootCA.pem")))
        try:
            self.php.write_cli_wrapper(self.cfg.default_php)
            self.php.install_composer(self.fetch)
            steps.append(("PHP CLI + Composer", Result(0)))
        except Exception as e:  # noqa: BLE001 - reported to the UI as-is
            steps.append(("PHP CLI + Composer", Result(1, "", str(e))))
        self.shell.install()
        self.apply()
        steps.append(("Start services", self.start_all()))
        return steps

    def start_all(self) -> Result:
        """Start the web server, the PHP versions in use, and the installed servers (database, Mailpit, Redis);
        verify that they stay up and explain what went wrong if they do not."""
        self.apply()
        in_use, tools = self.php_in_use(), self.wanted_tools()
        rs = self.services.start_all(self.cfg, in_use, tools)
        bad = next((r for r in rs if not r.ok), None)
        if bad:
            return bad
        failed = self.services.failures(self.services.core_services(self.cfg, in_use) + tools)
        return Result(1, "", self.explain_failures(failed)) if failed else Result(0)

    def explain_failures(self, failed: dict[str, str]) -> str:
        parts = []
        for name, log in failed.items():
            parts.append(f"{service_title(name, self.db_label)} did not start:\n    " + log.replace("\n", "\n    "))
        cfg = self.cfg
        for service, port in ((NGINX, cfg.http_port), (NGINX, cfg.https_port), (DNS, cfg.dns_port)):
            if service in failed and ("in use" in failed[service] or "bind()" in failed[service]):
                who = ports.owner(self.runner, port)
                if who:
                    parts.append(f"Port {port} is used by {who}. {ports.advice(who)}")
                    break
        return "\n\n".join(parts)

    def set_ports(self, http: int, https: int) -> SyncReport:
        """Move the web server to other ports (needed when Docker/DDEV or another server already uses 80/443)."""
        if not (1 <= http <= 65535 and 1 <= https <= 65535) or http == https:
            raise ControllerError("Use two different ports between 1 and 65535")
        self.cfg.http_port, self.cfg.https_port = http, https
        self.save()
        rep = self.apply()
        for s in self.sites():
            self.sync_app_url(s)
        return rep

    def stop_all(self) -> Result:
        """Stop everything for now. The tools that were on stay remembered, so they come back with "Start all"
        and at the next login."""
        return self.services.stop_all(self.cfg)

    def failure_report(self, name: str) -> str:
        """Why a service is not running, in words: its last log lines plus who holds its port."""
        r = self.runner.run(["journalctl", "--user", "-u", unit_name(name), "-n", "8", "--no-pager", "-o", "cat"])
        lines = [l for l in r.out.splitlines()
                 if l.strip() and not l.startswith(("Started", "Starting", "Stopped", "Stopping"))]
        return self.explain_failures({name: "\n".join(lines[-3:]) or "no log output"})

    def opened(self, site_name: str) -> None:
        """Remember a site that was just opened, for the tray menu."""
        from .config import RECENT
        recent = [site_name] + [s for s in self.cfg.recent_sites if s != site_name]
        if recent[:RECENT] != self.cfg.recent_sites:
            self.cfg.recent_sites = recent[:RECENT]
            self.save()

    def recent_sites(self, limit: int = 8) -> list[Site | Proxy]:
        """Recently opened sites first, then the others alphabetically; at most `limit`."""
        everything = {s.name: s for s in [*self.sites(), *self.proxies()]}
        names = [n for n in self.cfg.recent_sites if n in everything]
        names += [n for n in sorted(everything) if n not in names]
        return [everything[n] for n in names[:limit]]

    # ---------- one service ----------
    def wanted_tools(self) -> list[str]:
        """Installed database/Mailpit/Redis that the user did not turn off."""
        return [s for s in self.services.addons() if s not in self.cfg.stopped_tools]

    def start_service(self, name: str) -> Result:
        r = self.services.start(name)
        self._remember(name, True)
        return r

    def stop_service(self, name: str) -> Result:
        r = self.services.stop(name)
        self._remember(name, False)
        return r

    def restart_service(self, name: str) -> Result:
        r = self.services.restart(name)
        self._remember(name, True)
        return r

    def _remember(self, name: str, on: bool) -> None:
        """A tool that the user stops stays off ("Start all", login) until they start it again."""
        if name not in ADDONS:
            return
        stopped = [s for s in self.cfg.stopped_tools if s != name] + ([] if on else [name])
        if stopped != self.cfg.stopped_tools:
            self.cfg.stopped_tools = stopped
            self.save()
        if not on:
            self.services.disable(name)
        elif self.cfg.autostart_services:
            self.services.enable(name)

    def service_states(self) -> dict[str, str]:
        """State of every PIPanel service: the web server, every installed PHP version, the installed servers,
        and tunnels and site processes while they run (or failed)."""
        names = self.services.core_services(self.cfg) + self.services.addons()
        extra = [n for n in self.services.unit_services() if n in TUNNELS or split_site_process(n)]
        st = self.services.status(names + extra)
        return {n: s for n, s in st.items() if n in names or s in ("active", "activating", "failed")}

    def set_autostart_services(self, on: bool) -> None:
        self.cfg.autostart_services = on
        self.save()
        watcher = [path_unit_name(watch.SERVICE)] if (self.paths.systemd_user /
                                                       path_unit_name(watch.SERVICE)).exists() else []
        if on:
            self.services._ctl("enable", *self.services.core_services(self.cfg, self.php_in_use()),
                               *self.wanted_tools())
        else:
            self.services._ctl("disable", *self.services.core_services(self.cfg), *self.services.addons())
        if watcher:
            self.runner.run(["systemctl", "--user", "enable" if on else "disable", *watcher])

    def install_cli_launcher(self, python: str) -> Path:
        """`pipanel` command for terminals (next to `php` and `composer`)."""
        f = self.paths.bin / "pipanel"
        text = f'#!/bin/sh\nexec "{python}" -m pipanel "$@"\n'
        if not f.exists() or f.read_text() != text:
            self.paths.bin.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
            f.chmod(0o755)
        return f

    def set_autostart_app(self, on: bool) -> None:
        autostart.enable(self.config_home) if on else autostart.disable(self.config_home)

    def install_composer(self) -> Path:
        if not self.php.cli_version():
            self.php.write_cli_wrapper(self.cfg.default_php)
        return self.php.install_composer(self.fetch)

    def mysql_available(self) -> bool:
        return Path(self.mysql.mysqld).exists()

    # ---------- Node ----------
    def install_fnm(self) -> None:
        self.node.install_fnm(self.fetch)
        self.shell.install()

    # ---------- database (MySQL or MariaDB) ----------
    @property
    def db_label(self) -> str:
        return self.mysql.label

    def set_db_engine(self, engine: str) -> list[tuple[str, Result]]:
        """Switch between MySQL and MariaDB. They share the port, so the running one is stopped first and the
        new one is started if the old one was running. Each engine keeps its own data; nothing is converted."""
        if engine not in ENGINES:
            raise ControllerError(f"unknown database engine: {engine}")
        if engine == self.cfg.db_engine:
            return []
        steps: list[tuple[str, Result]] = []
        was_running = self.services.status([MYSQL]).get(MYSQL) == "active"
        if was_running:
            steps.append((f"Stop {self.db_label}", self.services.stop(MYSQL)))
        self.cfg.db_engine = engine
        self.save()
        self.mysql = MySQL(self.paths, self.runner, port=self.cfg.mysql_port, engine=engine)
        self._write_db_tools()
        if self.mysql_available() and self.mysql.initialized():
            self.mysql.write_config()
            self.services.write_extra_unit(MYSQL, **self.mysql.unit_spec())
            if was_running:
                steps.append((f"Start {self.db_label}", self.services.start(MYSQL)))
        return steps

    def install_database(self) -> list[tuple[str, Result]]:
        label = self.db_label
        steps = [(f"{label} packages + AppArmor", self.priv.install_database(self.cfg.db_engine))]
        if not steps[0][1].ok:
            return steps
        try:
            if not self.mysql.initialized():
                self.mysql.initialize()
            self.mysql.write_config()
            self.services.write_extra_unit(MYSQL, **self.mysql.unit_spec())
            steps.append(("Initialize data", Result(0)))
        except Exception as e:  # noqa: BLE001
            steps.append(("Initialize data", Result(1, "", str(e))))
            return steps
        started = self.start_service(MYSQL)
        steps.append((f"Start {label}", started))
        if started.ok:
            try:
                if self.mysql.wait_ready():
                    self.mysql.drop_extra_root_accounts()
                steps.append(("Secure the root account", Result(0)))
            except Exception as e:  # noqa: BLE001
                steps.append(("Secure the root account", Result(1, "", str(e))))
        return steps

    # ---------- tools: Adminer, Mailpit, Redis ----------
    def install_adminer(self) -> SyncReport:
        self.adminer.install(self.fetch)
        return self.apply()

    def install_phpmyadmin(self) -> SyncReport:
        self.phpmyadmin.install(self.fetch)
        return self.apply()

    def install_mailpit(self) -> Result:
        self.mailpit.install(self.fetch)
        self.services.write_extra_unit(MAIL, **self.mailpit.unit_spec())
        self.apply()
        return self.start_service(MAIL)

    def install_redis(self) -> list[tuple[str, Result]]:
        steps = [("Redis package", self.priv.install_redis())]
        if not steps[0][1].ok:
            return steps
        self.redis.write_config()
        self.services.write_extra_unit(REDIS, **self.redis.unit_spec())
        steps.append(("Start Redis", self.start_service(REDIS)))
        return steps

    # ---------- site processes (Vite, queue, scheduler, ...) ----------
    def site_processes(self, site_name: str) -> list[processes.Process]:
        return processes.available(self.site(site_name))

    def process_states(self, site_name: str) -> dict[str, str]:
        keys = [p.key for p in self.site_processes(site_name)]
        st = self.services.status([site_process_service(site_name, k) for k in keys])
        return {k: st[site_process_service(site_name, k)] for k in keys}

    def start_process(self, site_name: str, key: str) -> None:
        site = self.site(site_name)
        if key not in {p.key for p in processes.available(site)}:
            raise ControllerError(f"{key} is not available for {site_name}")
        try:
            spec = processes.unit_spec(site, key, self.php, self.node)
        except processes.ProcessError as e:
            raise ControllerError(str(e)) from e
        name = site_process_service(site_name, key)
        self.services.write_extra_unit(name, **spec)
        self.services.restart(name)
        failed = self.services.failures([name], settle=2.0)
        if failed:
            self.services.stop(name)   # do not leave it restarting in a loop
            raise ControllerError(f"{service_title(name)} stopped right away:\n{failed[name]}")

    def stop_process(self, site_name: str, key: str) -> Result:
        return self.services.stop(site_process_service(site_name, key))

    # ---------- Laravel ----------
    def new_laravel(self, name: str, parent: Path | None = None, with_mysql: bool = True) -> Path:
        self.last_notes: list[str] = []   # problems after the project itself was created (shown by the UI)
        parent = parent or (Path(self.cfg.parked[0]) if self.cfg.parked else None)
        if parent is None:
            raise ControllerError("park a folder first")
        try:
            target = projects.create_laravel(self.runner, self.paths, parent, name)
            slug = slugify(target.name)
            if slug:   # APP_URL=http://localhost -> the project's own .test address
                kind = LARAVEL if (target / "artisan").is_file() else "php"
                site = Site(slug, target, target / "public", kind, self.cfg.php_for(slug), self.cfg.site(slug).https)
                self.sync_app_url(site)
            if with_mysql and self.mysql.initialized():
                db = slugify(name).replace("-", "_") if slugify(name) else "laravel"
                self.mysql.create_database(db)
                projects.set_env(target, projects.mysql_env(self.cfg.mysql_port, db, self.mysql.root_password()))
                # the new project stores sessions/cache/queue in the database: create those tables right away
                try:
                    m = self.laravel.migrate(target, self.cfg.default_php)
                    if not m.ok:
                        self.last_notes.append(f"The project was created, but `php artisan migrate` failed: {m.text[-300:]}")
                except Exception as e:  # noqa: BLE001
                    self.last_notes.append(f"The project was created, but migrations did not run: {e}")
        except Exception as e:  # noqa: BLE001
            raise ControllerError(str(e)) from e
        return target

    # ---------- WordPress ----------
    def new_wordpress(self, name: str, parent: Path | None = None) -> NewSite:
        """A ready-to-use WordPress site with its own database. Returns the admin sign-in."""
        parent = parent or (Path(self.cfg.parked[0]) if self.cfg.parked else None)
        if parent is None:
            raise ControllerError("park a folder first")
        slug = slugify(name)
        if not slug:
            raise ControllerError(f"invalid project name: {name!r}")
        if not self.mysql.initialized() or not self.mysql.ping():
            raise ControllerError(f"WordPress needs the database: install and start {self.db_label} on the "
                                  "Database page first.")
        db = slug.replace("-", "_")
        if db in self.mysql.databases():
            raise ControllerError(f"database {db} already exists")
        try:
            if not self.wordpress.installed():
                self.wordpress.install(self.fetch)
            self.mysql.create_database(db)
            site = self.wordpress.create(parent, name, php_version=self.cfg.php_for(slug),
                                         url=self.host_url(slug, self.cfg.site(slug).https), db_name=db,
                                         db_port=self.cfg.mysql_port, db_password=self.mysql.root_password())
        except (WordPressError, OSError) as e:
            raise ControllerError(str(e)) from e
        self.apply()
        return site

    # ---------- a Laravel project fresh from Git ----------
    def setup_needs(self, site_name: str) -> list[str]:
        site = self.site(site_name)
        return projects.setup_needs(site.path) if site.kind == LARAVEL else []

    def setup_laravel(self, site_name: str, *, database: str = "migrate", dump: Path | None = None,
                      npm: bool = False) -> list[tuple[str, Result]]:
        """Make a cloned project run: composer install, .env, APP_KEY, its database (created, then migrated or
        loaded from a dump), storage:link, and optionally npm install. Stops when a step the rest depends on fails.
        `database`: "migrate", "import" (with `dump`), or "none"."""
        site = self.site(site_name)
        path, v = site.path, site.php
        steps: list[tuple[str, Result]] = []

        def step(name: str, fn) -> bool:
            try:
                r = fn()
                r = r if isinstance(r, Result) else Result(0)
            except Exception as e:  # noqa: BLE001 - shown as a failed step
                r = Result(1, "", str(e))
            steps.append((name, r))
            return r.ok

        needs = projects.setup_needs(path)
        if "vendor" in needs and not step("composer install", lambda: self.laravel.composer(path, v, "install")):
            return steps
        if "env" in needs:
            if not step("Create .env from .env.example", lambda: projects.set_env(path, {})):
                return steps
            conn = projects.dotenv_get(path, "DB_CONNECTION")
            if conn in ("mysql", "mariadb") and self.mysql.initialized():
                db = projects.dotenv_get(path, "DB_DATABASE") or slugify(site.name).replace("-", "_")
                step(f"Point .env at the PIPanel {self.db_label} (database {db})",
                     lambda: projects.set_env(path, projects.mysql_env(self.cfg.mysql_port, db,
                                                                         self.mysql.root_password())))
            self.sync_app_url(site)
        if not projects.dotenv_get(path, "APP_KEY"):
            if not step("php artisan key:generate", lambda: self.laravel.artisan(path, v, "key:generate", "--force")):
                return steps

        conn = projects.dotenv_get(path, "DB_CONNECTION") or "sqlite"
        db = projects.dotenv_get(path, "DB_DATABASE")
        ready = False
        if conn in ("mysql", "mariadb"):
            host = projects.dotenv_get(path, "DB_HOST") or "127.0.0.1"
            if host not in ("127.0.0.1", "localhost"):
                steps.append(("Database", Result(1, "", f"DB_HOST is {host}: that server is not managed by PIPanel")))
            elif not db:
                steps.append(("Database", Result(1, "", "DB_DATABASE is not set in .env")))
            elif not (self.mysql.initialized() and self.mysql.ping()):
                steps.append(("Database", Result(1, "", f"{self.db_label} is not running: start it on the Database "
                                                        "page, then click Set up again")))
            elif db in self.mysql.databases():
                ready = True
            else:
                ready = step(f"Create database {db}", lambda: self.mysql.create_database(db))
        elif conn == "sqlite":
            f = Path(db) if db and Path(db).is_absolute() else path / "database" / "database.sqlite"
            ready = step(f"Create {f.relative_to(path) if path in f.parents else f}",
                         lambda: (f.parent.mkdir(parents=True, exist_ok=True), f.touch()))
        if ready and database == "import" and dump and conn in ("mysql", "mariadb"):
            step(f"Import {Path(dump).name} into {db}", lambda: self.mysql.import_file(db, Path(dump)))
        elif ready and database == "migrate":
            step("php artisan migrate", lambda: self.laravel.migrate(path, v))

        if (path / "storage" / "app" / "public").is_dir() and not (path / "public" / "storage").exists():
            step("php artisan storage:link", lambda: self.laravel.artisan(path, v, "storage:link"))
        if npm and (path / "package.json").is_file() and not (path / "node_modules").is_dir():
            if not self.node.available():
                steps.append(("npm install", Result(1, "", "Node.js is managed with fnm: Node page -> Install fnm")))
            else:
                cmd = "ci" if (path / "package-lock.json").is_file() else "install"
                using = [] if processes.node_version_file(path) else ["--using", "default"]
                step(f"npm {cmd}", lambda: self.runner.run([str(self.node.fnm_bin), "exec", *using, "npm", cmd],
                                                           cwd=str(path), env={"FNM_DIR": str(self.node.fnm_dir)},
                                                           timeout=1800))
        return steps

    # ---------- Laravel packages ----------
    def laravel_sites(self) -> list[Site]:
        from .sites import LARAVEL
        return [s for s in self.sites() if s.kind == LARAVEL]

    # ---------- ngrok ----------
    def ngrok_state(self) -> str:
        """'missing' (no token), 'unverified' (saved but not checked with ngrok yet), or 'verified'."""
        token = self.ngrok.read_token()
        if not token:
            return "missing"
        return "verified" if self.cfg.ngrok_verified == ngrok_fingerprint(token) else "unverified"

    def verify_ngrok(self) -> TokenCheck:
        """Ask ngrok whether the saved token works. Remembers the answer for that exact token."""
        token = self.ngrok.read_token()
        if not token:
            raise ControllerError("Save your ngrok authtoken first.")
        if self.services.status([NGROK]).get(NGROK) == "active":
            raise ControllerError("Stop sharing first: a free ngrok account allows one session at a time.")
        if not self.ngrok.installed():
            self.ngrok.install(self.fetch)
        result = self.ngrok.verify(port=self.cfg.http_port)
        self.cfg.ngrok_verified = ngrok_fingerprint(token) if result.ok else ""
        self.save()
        return result

    def set_share_provider(self, provider: str) -> None:
        if provider not in ("ngrok", "cloudflare"):
            raise ControllerError(f"unknown provider: {provider}")
        self.cfg.share_provider = provider
        self.save()

    def share_state(self) -> str | None:
        """The tunnel that is running (ngrok / cloudflared), or None."""
        st = self.services.status(list(TUNNELS))
        return next((n for n in TUNNELS if st.get(n) in ("active", "activating")), None)

    def share(self, site: str) -> None:
        """Start a public tunnel to a site (or proxy) with the chosen provider. Only one tunnel runs at a time."""
        if site not in {s.name for s in self.sites()} | set(self.cfg.proxies):
            raise ControllerError(f"site not found: {site}")
        if self.cfg.share_provider == "cloudflare":
            self.services.stop(NGROK)
            if not self.cloudflared.installed():
                self.cloudflared.install(self.fetch)
            self.services.write_extra_unit(CLOUDFLARED, **self.cloudflared.unit_spec(site, self.cfg.tld,
                                                                                    self.cfg.http_port))
            self.services.restart(CLOUDFLARED)
            failed = self.services.failures([CLOUDFLARED], settle=4.0)
            if failed:
                self.services.stop(CLOUDFLARED)
                raise ControllerError(f"cloudflared did not start:\n{failed[CLOUDFLARED]}")
            return
        self._share_ngrok(site)

    def _share_ngrok(self, site: str) -> None:
        """Needs an ngrok authtoken that was verified."""
        state = self.ngrok_state()
        if state == "missing":
            raise ControllerError("ngrok needs an authtoken. Create a free account at ngrok.com, copy the token "
                                  "from dashboard.ngrok.com, and save it under Share -> ngrok account.")
        if state == "unverified":
            raise ControllerError("Verify your ngrok token first (Share -> ngrok account -> Verify token).")
        self.services.stop(CLOUDFLARED)
        if not self.ngrok.installed():
            self.ngrok.install(self.fetch)
        self.services.write_extra_unit(NGROK, **self.ngrok.unit_spec(site, self.cfg.tld, self.cfg.http_port))
        self.services.restart(NGROK)
        failed = self.services.failures([NGROK], settle=4.0)
        if failed:
            self.services.stop(NGROK)   # do not leave it restarting in a loop
            raise ControllerError(f"ngrok did not start:\n{failed[NGROK]}")

    def stop_share(self) -> None:
        self.services._ctl("stop", *TUNNELS)

    def share_url(self) -> str | None:
        running = self.share_state()
        if running == CLOUDFLARED:
            return self.cloudflared.public_url(self.fetch)
        return self.ngrok.public_url(self.fetch) if running == NGROK else None

    # ---------- diagnostics ----------
    def doctor(self) -> list[Check]:
        d = Doctor(self.paths, self.runner, self.cfg, self.services, self.php, self.certs, self.priv, db=self.mysql)
        return d.run_all({s.php for s in self.sites()}, self.php_in_use())

    def fix(self, fix_id: str) -> Result:
        if fix_id == "setup":
            return self.setup()[-1][1]
        if fix_id == "fix_system_services":
            return self.priv.fix_system_services()
        if fix_id == "start_all":
            return self.start_all()
        if fix_id == "install_ca":
            r = self.certs.install_ca_user()
            root = self.certs.caroot()
            return self.priv.trust_ca(root / "rootCA.pem") if r.ok and root else r
        if fix_id == "install_db_tools":
            if self.priv.outdated():
                return Result(1, "", "The root helper is outdated: run ./install.sh again, then try once more.")
            return self.priv.install_database(self.cfg.db_engine)
        if fix_id.startswith("install_php:"):
            return self.install_php(fix_id.split(":", 1)[1])
        return Result(1, "", f"unknown fix: {fix_id}")
