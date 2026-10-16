"""Glue between all core modules. The UI only calls this class."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from . import config as cfgmod
from . import ports
from . import autostart, projects
from .certs import Certs, CertError
from .config import Config, SiteOverride
from .doctor import Check, Doctor
from .laravel import LaravelTools
from .mysql import ENGINES, MySQL
from .ngrok import Ngrok, TokenCheck
from .ngrok import fingerprint as ngrok_fingerprint
from .node import Node, ShellIntegration
from .paths import Paths
from .php import PHP, SUPPORTED, http_fetch
from .privileged import Privileged
from .runner import Result, Runner
from .services import DNS, MYSQL, NGINX, NGROK, Services, SyncReport, php_service, service_title, unit_name
from .sites import Site, relative_docroot, safe_subdir, scan, slugify
from .templates import valid_site_name


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

    def apply(self) -> SyncReport:
        """Rewrite the config from current state, then reload the running services."""
        rep = self.services.sync(self.cfg, self.sites())
        if rep.changed:
            names = [NGINX] + [php_service(v) for v in self.php.installed()]
            active = [n for n, s in self.services.status(names).items() if s == "active"]
            for n in active:
                self.services.reload(n)
        return rep

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
        return self.apply()

    def site_url(self, s: Site) -> str:
        scheme, port, default = ("https", self.cfg.https_port, 443) if s.https else ("http", self.cfg.http_port, 80)
        return f"{scheme}://{s.name}.{self.cfg.tld}" + ("" if port == default else f":{port}")

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
        """Start the core services and verify that they stay up; explain what went wrong if they do not."""
        self.apply()
        rs = self.services.start_all(self.cfg)
        bad = next((r for r in rs if not r.ok), None)
        if bad:
            return bad
        failed = self.services.failures(self.services.core_services(self.cfg))
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
        return self.apply()

    def stop_all(self) -> Result:
        return self.services.stop_all(self.cfg)

    def service_states(self) -> dict[str, str]:
        names = self.services.core_services(self.cfg)
        if (self.paths.systemd_user / unit_name(MYSQL)).exists():
            names.append(MYSQL)
        return self.services.status(names)

    def set_autostart_services(self, on: bool) -> None:
        self.cfg.autostart_services = on
        self.save()
        names = self.services.core_services(self.cfg)
        self.services._ctl("enable" if on else "disable", *names)

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
        started = self.services.start(MYSQL)
        steps.append((f"Start {label}", started))
        if started.ok:
            try:
                if self.mysql.wait_ready():
                    self.mysql.drop_extra_root_accounts()
                steps.append(("Secure the root account", Result(0)))
            except Exception as e:  # noqa: BLE001
                steps.append(("Secure the root account", Result(1, "", str(e))))
        return steps

    # ---------- Laravel ----------
    def new_laravel(self, name: str, parent: Path | None = None, with_mysql: bool = True) -> Path:
        self.last_notes: list[str] = []   # problems after the project itself was created (shown by the UI)
        parent = parent or (Path(self.cfg.parked[0]) if self.cfg.parked else None)
        if parent is None:
            raise ControllerError("park a folder first")
        try:
            target = projects.create_laravel(self.runner, self.paths, parent, name)
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

    def share(self, site: str) -> None:
        """Start a public tunnel to a site. Needs an ngrok authtoken that was verified."""
        state = self.ngrok_state()
        if state == "missing":
            raise ControllerError("ngrok needs an authtoken. Create a free account at ngrok.com, copy the token "
                                  "from dashboard.ngrok.com, and save it under Share -> ngrok account.")
        if state == "unverified":
            raise ControllerError("Verify your ngrok token first (Share -> ngrok account -> Verify token).")
        self.site(site)
        if not self.ngrok.installed():
            self.ngrok.install(self.fetch)
        self.services.write_extra_unit(NGROK, **self.ngrok.unit_spec(site, self.cfg.tld, self.cfg.http_port))
        self.services.restart(NGROK)
        failed = self.services.failures([NGROK], settle=4.0)
        if failed:
            self.services.stop(NGROK)   # do not leave it restarting in a loop
            raise ControllerError(f"ngrok did not start:\n{failed[NGROK]}")

    def stop_share(self) -> None:
        self.services.stop(NGROK)

    def share_url(self) -> str | None:
        return self.ngrok.public_url(self.fetch)

    # ---------- diagnostics ----------
    def doctor(self) -> list[Check]:
        d = Doctor(self.paths, self.runner, self.cfg, self.services, self.php, self.certs, self.priv)
        return d.run_all({s.php for s in self.sites()})

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
        if fix_id.startswith("install_php:"):
            return self.install_php(fix_id.split(":", 1)[1])
        return Result(1, "", f"unknown fix: {fix_id}")
