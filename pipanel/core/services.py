"""Write config + systemd user units and control services through `systemctl --user`."""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import templates as T
from .config import Config
from .paths import Paths
from .php import PHP
from .runner import Result, Runner
from .sites import Site

NGINX, DNS, MYSQL, NGROK = "nginx", "dns", "mysql", "ngrok"
SBIN_DIRS = ("/usr/sbin", "/usr/bin", "/usr/local/bin", "/sbin")


def service_title(name: str, db_label: str = "MySQL") -> str:
    """Human name of a service id (nginx, dns, php8.4, mysql, ngrok)."""
    if name.startswith("php"):
        return f"PHP-FPM {name[3:]}"
    return {"nginx": "Nginx", "dns": "DNS (dnsmasq, *.test)", "mysql": db_label, "ngrok": "ngrok"}.get(name, name)


class ServiceError(Exception):
    pass


def php_service(v: str) -> str:
    return f"php{v}"


# Real tests run against the user's own systemd manager, so they must use unit names of their own
# (PIPANEL_UNIT_PREFIX=pipanel-e2e) and never touch the units of a real installation.
UNIT_PREFIX = os.environ.get("PIPANEL_UNIT_PREFIX", "pipanel")


def unit_name(service: str) -> str:
    return f"{UNIT_PREFIX}-{service}.service"


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
                 nginx_bin: str | None = None, etc_nginx: Path | str = "/etc/nginx"):
        self.paths, self.runner, self.php = paths, runner, php
        self._nginx_version = nginx_version
        self.etc_nginx = etc_nginx
        self.nginx_bin = nginx_bin or find_bin("nginx")

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
    def sync(self, cfg: Config, sites: list[Site]) -> SyncReport:
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
        changed = _write_if_changed(p.nginx_dir / "nginx.conf", T.render_nginx_conf(p, self.etc_nginx, cfg.http_port))
        want: set[Path] = set()
        upload = cfg.php_settings.get("upload_max_filesize", "128M").lower()
        for s in sites:
            https = s.https
            if https and not (p.certs / f"{s.name}.{cfg.tld}.pem").exists():
                https = False
                rep.warnings.append(f"{s.name}: certificate is missing, HTTPS is temporarily disabled")
            if s.php not in installed:
                rep.warnings.append(f"{s.name}: PHP {s.php} is not installed")
            f = p.nginx_sites / f"{s.name}.conf"
            want.add(f)
            changed |= _write_if_changed(f, T.render_site(
                p, name=s.name, docroot=s.docroot, php_version=s.php, tld=cfg.tld, https=https,
                upload_max=upload, nginx_version=nginx_ver, etc_nginx=self.etc_nginx,
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
            rep.changed |= _write_if_changed(p.php_dir(v) / "php-fpm.conf", T.render_php_fpm(p, v))

        # dnsmasq
        rep.changed |= _write_if_changed(p.dns_dir / "dnsmasq.conf",
                                         T.render_dnsmasq(p, port=cfg.dns_port, tld=cfg.tld))

        units_changed = self._write_units(needed)
        if units_changed:
            self.runner.run(["systemctl", "--user", "daemon-reload"])
        rep.changed |= units_changed
        return rep

    def _mkrun(self) -> str:
        return f"/bin/mkdir -p {self.paths.run} {self.paths.logs}"

    def _write_units(self, php_versions: set[str]) -> bool:
        p, changed = self.paths, False
        n = self._nginx_args()
        specs = {
            NGINX: dict(description="PIPanel: Nginx", exec_start=" ".join(n),
                        exec_reload=" ".join(n + ["-s", "reload"]), exec_pre=[self._mkrun()]),
            DNS: dict(description="PIPanel: dnsmasq (*.test)",
                      exec_start=f"{find_bin('dnsmasq')} --keep-in-foreground "
                                 f"--conf-file={p.dns_dir / 'dnsmasq.conf'}",
                      exec_pre=[self._mkrun()]),
        }
        for v in php_versions:
            specs[php_service(v)] = dict(
                description=f"PIPanel: PHP-FPM {v}",
                exec_start=f"{self.php.fpm_bin(v)} --nodaemonize --fpm-config {p.php_dir(v) / 'php-fpm.conf'}",
                exec_reload="/bin/kill -USR2 $MAINPID", env=self.php.scan_env(v), exec_pre=[self._mkrun()])
        for name, spec in specs.items():
            changed |= _write_if_changed(p.systemd_user / unit_name(name), T.render_unit(**spec))
        return changed

    def write_extra_unit(self, name: str, **spec) -> bool:
        changed = _write_if_changed(self.paths.systemd_user / unit_name(name), T.render_unit(**spec))
        if changed:
            self.runner.run(["systemctl", "--user", "daemon-reload"])
        return changed

    # ---------- kontrol ----------
    def _ctl(self, verb: str, *services: str) -> Result:
        return self.runner.run(["systemctl", "--user", verb, *[unit_name(s) for s in services]])

    def start(self, s: str) -> Result: return self._ctl("start", s)
    def stop(self, s: str) -> Result: return self._ctl("stop", s)
    def restart(self, s: str) -> Result: return self._ctl("restart", s)
    def enable(self, s: str) -> Result: return self._ctl("enable", s)
    def disable(self, s: str) -> Result: return self._ctl("disable", s)

    def reload(self, s: str) -> Result:
        r = self._ctl("reload", s)
        return r if r.ok else self._ctl("restart", s)

    def status(self, services: list[str]) -> dict[str, str]:
        """active / inactive / failed / not-found."""
        if not services:
            return {}
        r = self.runner.run(["systemctl", "--user", "is-active", *[unit_name(s) for s in services]])
        lines = r.out.split()
        states = {s: (lines[i] if i < len(lines) else "unknown") for i, s in enumerate(services)}
        # a unit that was never written is reported "inactive" by is-active; tell them apart by the file
        for s in services:
            if not (self.paths.systemd_user / unit_name(s)).exists() and states[s] == "inactive":
                states[s] = "not-found"
        return states

    def core_services(self, cfg: Config) -> list[str]:
        return [NGINX, DNS, *[php_service(v) for v in self.php.installed()]]

    def start_all(self, cfg: Config) -> list[Result]:
        names = self.core_services(cfg)
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
            r = self.runner.run(["journalctl", "--user", "-u", unit_name(n), "-n", "6", "--no-pager", "-o", "cat"])
            lines = [l for l in r.out.splitlines() if l.strip() and not l.startswith(("Started", "Starting", "Stopped"))]
            out[n] = "\n".join(lines[-3:]) or states.get(n, "unknown")
        return out

    def stop_all(self, cfg: Config) -> Result:
        return self._ctl("stop", *self.core_services(cfg))
