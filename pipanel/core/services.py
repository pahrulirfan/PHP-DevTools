"""Write the config of Nginx, PHP and DNS, and control the services through the platform's supervisor
(systemd user units on Linux, the PIPanel agent on Windows and macOS)."""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import platform
from . import templates as T
from .config import Config
from .paths import Paths
from .php import PHP
from .runner import Result, Runner
from .sites import Proxy, Site
from .supervisor import UNIT_PREFIX, Supervisor, for_platform, path_unit_name, unit_name  # noqa: F401 (re-exported)

NGINX, DNS, MYSQL, NGROK = "nginx", "dns", "mysql", "ngrok"
MAIL, REDIS, CLOUDFLARED = "mail", "redis", "cloudflared"
ADDONS = (MYSQL, MAIL, REDIS)        # optional servers: started with "Start all" unless the user turned them off
TUNNELS = (NGROK, CLOUDFLARED)
SITE_PREFIX = "site-"                # per-site processes: site-<site>-<process>, e.g. site-blog-vite
SBIN_DIRS = ("/usr/sbin", "/usr/bin", "/usr/local/bin", "/sbin")
PROCESS_TITLES = {"vite": "Vite", "queue": "Queue worker", "schedule": "Scheduler", "reverb": "Reverb",
                  "horizon": "Horizon"}


def site_process_service(site: str, process: str) -> str:
    return f"{SITE_PREFIX}{site}-{process}"


def split_site_process(name: str) -> tuple[str, str] | None:
    """site-my-blog-vite -> ("my-blog", "vite"); None for any other service id."""
    if not name.startswith(SITE_PREFIX) or "-" not in name[len(SITE_PREFIX):]:
        return None
    site, _, proc = name[len(SITE_PREFIX):].rpartition("-")
    return (site, proc) if site else None


def service_title(name: str, db_label: str = "MySQL") -> str:
    """Human name of a service id (nginx, dns, php8.4, mysql, ngrok, site-blog-vite, ...)."""
    if name.startswith("php"):
        return f"PHP {name[3:]}" if platform.is_windows() else f"PHP-FPM {name[3:]}"
    sp = split_site_process(name)
    if sp:
        return f"{sp[0]}: {PROCESS_TITLES.get(sp[1], sp[1])}"
    return {"nginx": "Nginx", "dns": "DNS (*.test)" if platform.current() != platform.LINUX else "DNS (dnsmasq, *.test)",
            "mysql": db_label, "ngrok": "ngrok",
            "mail": "Mailpit", "redis": "Redis", "cloudflared": "Cloudflare Tunnel",
            "watch": "New folder watcher"}.get(name, name)


class ServiceError(Exception):
    pass


def php_service(v: str) -> str:
    return f"php{v}"


def find_bin(name: str) -> str:
    for d in SBIN_DIRS:
        p = Path(d) / name
        if p.exists():
            return str(p)
    return shutil.which(name) or f"/usr/sbin/{name}"


@dataclass
class SyncReport:
    changed: bool = False
    warnings: list[str] = field(default_factory=list)


def _write_if_changed(path: Path, text: str, mode: int | None = None) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode is not None:
        path.chmod(mode)
    return True


class Services:
    def __init__(self, paths: Paths, runner: Runner, php: PHP, *, nginx_version: str | None = None,
                 nginx_bin: str | None = None, etc_nginx: Path | str = "/etc/nginx",
                 supervisor: Supervisor | None = None, dns_argv: list[str] | None = None):
        self.paths, self.runner, self.php = paths, runner, php
        self._nginx_version = nginx_version
        self.etc_nginx = etc_nginx
        self.nginx_bin = nginx_bin or find_bin("nginx")
        self.sup = supervisor or for_platform(paths, runner)
        self.dns_argv = dns_argv   # None: dnsmasq (Linux); otherwise PIPanel's own DNS server

    # ---------- nginx ----------
    def nginx_version(self) -> str:
        if self._nginx_version:
            return self._nginx_version
        r = self.runner.run([self.nginx_bin, "-v"])
        m = re.search(r"nginx/(\d+\.\d+\.\d+)", r.text)
        self._nginx_version = m.group(1) if m else "1.18.0"
        return self._nginx_version

    def _nginx_args(self) -> list[str]:
        d = self.paths.nginx_dir
        return [self.nginx_bin, "-p", str(d), "-c", str(d / "nginx.conf"), "-e", "stderr"]

    def nginx_test(self) -> Result:
        return self.runner.run(self._nginx_args() + ["-t"])

    # ---------- sinkronisasi config ----------
    def sync(self, cfg: Config, sites: list[Site], proxies: list[Proxy] = (),
             internal_sites: list[Site] = ()) -> SyncReport:
        """Write every config file. `internal_sites` are PIPanel's own PHP tools (Adminer), served to this
        computer only."""
        p, rep = self.paths, SyncReport()
        p.ensure()
        for sub in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"):
            (p.run / "nginx-tmp" / sub).mkdir(parents=True, exist_ok=True)

        installed = set(self.php.installed())
        needed = {s.php for s in sites} | {cfg.default_php} | installed   # a unit for every installed version
        nginx_ver = self.nginx_version()

        # nginx: keep the old contents so they can be restored if `nginx -t` fails
        old = {f: f.read_text() for f in [p.nginx_dir / "nginx.conf", *p.nginx_sites.glob("*.conf")]
               if f.exists()}
        upstreams = {self.php.upstream(v): self.php.upstream_servers(v) for v in sorted(needed & installed)
                     if self.php.upstream_servers(v)}   # Windows: php-cgi pools on TCP ports
        changed = _write_if_changed(p.nginx_dir / "nginx.conf",
                                    T.render_nginx_conf(p, self.etc_nginx, cfg.http_port, upstreams))
        want: set[Path] = set()
        upload = cfg.php_settings.get("upload_max_filesize", "128M").lower()

        def https_for(name: str, on: bool) -> bool:
            if on and not (p.certs / f"{name}.{cfg.tld}.pem").exists():
                rep.warnings.append(f"{name}: certificate is missing, HTTPS is temporarily disabled")
                return False
            return on

        internal = {s.name for s in internal_sites}
        for s in [*sites, *internal_sites]:
            if s.php not in installed:
                rep.warnings.append(f"{s.name}: PHP {s.php} is not installed")
            f = p.nginx_sites / f"{s.name}.conf"
            want.add(f)
            changed |= _write_if_changed(f, T.render_site(
                p, name=s.name, docroot=s.docroot, php_version=s.php, php_upstream=self.php.upstream(s.php),
                tld=cfg.tld, https=https_for(s.name, s.https),
                upload_max=upload, nginx_version=nginx_ver, etc_nginx=self.etc_nginx,
                http_port=cfg.http_port, https_port=cfg.https_port, local_only=s.name in internal))
        for x in proxies:
            f = p.nginx_sites / f"{x.name}.conf"
            want.add(f)
            changed |= _write_if_changed(f, T.render_proxy(
                p, name=x.name, target=x.target, tld=cfg.tld, https=https_for(x.name, x.https),
                local_only=x.local_only, upload_max=upload, nginx_version=nginx_ver,
                http_port=cfg.http_port, https_port=cfg.https_port))
        for f in p.nginx_sites.glob("*.conf"):
            if f not in want:
                f.unlink()
                changed = True

        if changed and Path(self.nginx_bin).exists():
            r = self.nginx_test()
            if not r.ok:
                for f in p.nginx_sites.glob("*.conf"):
                    f.unlink()
                for f, text in old.items():
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_text(text)
                raise ServiceError(f"nginx config rejected, changes rolled back:\n{r.text}")
        rep.changed |= changed

        # PHP-FPM + per-version conf.d
        for v in sorted(needed):
            self.php.ensure_conf_d(v, cfg.php_settings)
            if self.php.uses_fpm_conf:
                rep.changed |= _write_if_changed(p.php_dir(v) / "php-fpm.conf", T.render_php_fpm(p, v))

        # dnsmasq
        if self.dns_argv is None:
            rep.changed |= _write_if_changed(p.dns_dir / "dnsmasq.conf",
                                             T.render_dnsmasq(p, port=cfg.dns_port, tld=cfg.tld))

        rep.changed |= self._write_units(needed)
        return rep

    def _dirs(self) -> list[str]:
        return [str(self.paths.run), str(self.paths.logs)]

    def _write_units(self, php_versions: set[str]) -> bool:
        p = self.paths
        n = self._nginx_args()
        specs = {
            NGINX: dict(description="PIPanel: Nginx", argv=n, reload=n + ["-s", "reload"], dirs=self._dirs()),
            DNS: dict(description="PIPanel: DNS (*.test)",
                      argv=self.dns_argv or [find_bin("dnsmasq"), "--keep-in-foreground",
                                             f"--conf-file={p.dns_dir / 'dnsmasq.conf'}"],
                      dirs=self._dirs()),
        }
        for v in php_versions:
            specs[php_service(v)] = dict(description=f"PIPanel: PHP {v}", argv=self.php.server_argv(v),
                                         reload=self.php.reload_how, env=self.php.scan_env(v), dirs=self._dirs())
        return self.sup.write_many(specs)

    def remove_unit(self, name: str) -> None:
        self.sup.remove(name)

    def write_extra_unit(self, name: str, **spec) -> bool:
        return self.sup.write(name, spec)

    # ---------- control ----------
    def _ctl(self, verb: str, *services: str) -> Result:
        return self.sup.ctl(verb, *services)

    def start(self, s: str) -> Result: return self._ctl("start", s)
    def stop(self, s: str) -> Result: return self._ctl("stop", s)
    def restart(self, s: str) -> Result: return self._ctl("restart", s)
    def enable(self, s: str) -> Result: return self._ctl("enable", s)
    def disable(self, s: str) -> Result: return self._ctl("disable", s)
    def reload(self, s: str) -> Result: return self._ctl("reload", s)

    def status(self, services: list[str]) -> dict[str, str]:
        """active / activating / inactive / failed / not-found."""
        return self.sup.status(services)

    def core_services(self, cfg: Config, php_versions: list[str] | None = None) -> list[str]:
        """Nginx, DNS, and PHP-FPM for `php_versions` (default: every installed version)."""
        versions = self.php.installed() if php_versions is None else php_versions
        return [NGINX, DNS, *[php_service(v) for v in versions]]

    def has_unit(self, s: str) -> bool:
        return self.sup.has(s)

    def unit_services(self) -> list[str]:
        """Every service that has a unit, in a stable order."""
        return [n for n in self.sup.names() if n != "watch"]

    def addons(self) -> list[str]:
        """Installed optional servers (database, Mailpit, Redis)."""
        return [s for s in ADDONS if self.has_unit(s)]

    def start_all(self, cfg: Config, php_versions: list[str] | None = None,
                  addons: list[str] | None = None) -> list[Result]:
        names = self.core_services(cfg, php_versions) + (self.addons() if addons is None else addons)
        out = [self._ctl("start", *names)]
        if cfg.autostart_services:
            out.append(self._ctl("enable", *names))
        return out

    def failures(self, names: list[str], settle: float = 3.0, poll: float = 0.3) -> dict[str, str]:
        """Wait up to `settle` seconds for every service to be active; return {name: last log lines} for those that
        are not. (`systemctl start` reports success even when the process dies right after starting.)"""
        import time
        deadline = time.monotonic() + settle
        while True:
            states = self.status(names)
            bad = [n for n in names if states.get(n) != "active"]
            if not bad or time.monotonic() >= deadline:
                break
            time.sleep(poll)
        out = {}
        for n in bad:
            log = self.sup.last_lines(n, 6)
            lines = [l for l in log.splitlines()
                     if l.strip() and not l.startswith(("Started", "Starting", "Stopped", "--- "))]
            out[n] = "\n".join(lines[-3:]) or states.get(n, "unknown")
        return out

    def stop_all(self, cfg: Config) -> Result:
        """Stop everything PIPanel runs: web server, PHP, databases, tools, tunnels, and site processes."""
        names = list(dict.fromkeys(self.core_services(cfg) + self.unit_services()))
        return self._ctl("stop", *names)
