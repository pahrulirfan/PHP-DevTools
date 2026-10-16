"""Environment diagnostics."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import dns, ports
from .certs import Certs
from .config import Config
from .paths import Paths
from .php import PHP
from .privileged import Privileged
from .runner import Runner
from .services import NGINX, Services, find_bin

OK, WARN, FAIL = "ok", "warn", "fail"
RESOLVED_DROPIN = Path("/etc/systemd/resolved.conf.d/pipanel.conf")
SYSCTL_FILE = Path("/etc/sysctl.d/60-pipanel.conf")


@dataclass
class Check:
    id: str
    title: str
    status: str
    detail: str = ""
    fix: str | None = None   # id of a fix action known to the UI


def parse_listeners(ss_out: str) -> dict[int, str]:
    """`ss -H -ltnp` -> {port: process name ('' if not visible)}."""
    res: dict[int, str] = {}
    for line in ss_out.splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        m = re.search(r":(\d+)$", cols[3])
        if m:
            p = re.search(r'\(\("([^"]+)"', line)
            res[int(m.group(1))] = p.group(1) if p else ""
    return res


class Doctor:
    def __init__(self, paths: Paths, runner: Runner, cfg: Config, services: Services, php: PHP,
                 certs: Certs, priv: Privileged, *, resolved_dropin: Path = RESOLVED_DROPIN,
                 sysctl_file: Path = SYSCTL_FILE, query: Callable[..., list[str]] = dns.query_a,
                 sys_resolves: Callable[[str], bool] = dns.system_resolves):
        self.paths, self.runner, self.cfg = paths, runner, cfg
        self.services, self.php, self.certs, self.priv = services, php, certs, priv
        self.resolved_dropin, self.sysctl_file = resolved_dropin, sysctl_file
        self.query, self.sys_resolves = query, sys_resolves

    def run_all(self, site_php: set[str] | None = None) -> list[Check]:
        out = [self.helper(), self.packages(), self.sysctl(), self.system_services(), self.ports(),
               self.units(), self.dns(), self.ca() if self.certs.available() else self.https_tool()]
        out += self.sockets(site_php or set())
        return out

    def helper(self) -> Check:
        if self.priv.available():
            return Check("helper", "Root helper installed", OK)
        return Check("helper", "Root helper not installed", FAIL, "Run ./install.sh from the project folder.")

    def packages(self) -> Check:
        missing = [n for n in ("nginx", "dnsmasq") if not Path(find_bin(n)).exists()]
        if not self.php.installed():
            missing.append("php-fpm")
        if missing:
            return Check("packages", "Base packages incomplete", FAIL, ", ".join(missing), fix="setup")
        return Check("packages", "Base packages installed", OK)

    def https_tool(self) -> Check:
        """mkcert is optional: without it only the HTTPS toggle is unavailable."""
        return Check("mkcert", "HTTPS is unavailable: mkcert is not installed (plain http:// works)", WARN,
                     "Optional. Install it to get trusted https://*.test certificates.", fix="setup")

    def sysctl(self) -> Check:
        try:
            val = Path("/proc/sys/net/ipv4/ip_unprivileged_port_start").read_text().strip()
            ok = int(val) <= 80
        except (OSError, ValueError):
            ok = self.sysctl_file.exists()
        return Check("sysctl", "Ports 80/443 usable without root", OK) if ok else Check(
            "sysctl", "Ports 80/443 require root", FAIL, "ip_unprivileged_port_start > 80", fix="setup")

    def system_services(self) -> Check:
        bad = []
        for u in ["nginx", "mysql", *[f"php{v}-fpm" for v in self.php.installed()]]:
            if self.runner.run(["systemctl", "is-active", f"{u}.service"]).out.strip() == "active":
                bad.append(u)
        if bad:
            return Check("sysservices", "System services still active", WARN,
                         ", ".join(bad) + " (conflicts with the PIPanel versions)", fix="fix_system_services")
        return Check("sysservices", "Bundled system services are disabled", OK)

    def ports(self) -> Check:
        r = self.runner.run(["ss", "-H", "-ltnp"])
        ls = parse_listeners(r.out)
        ours = self.services.status([NGINX]).get(NGINX) == "active"
        notes, status = [], OK
        for port in (self.cfg.http_port, self.cfg.https_port):
            who = ls.get(port)
            if who is None:
                if ours and (port == self.cfg.http_port):
                    status = FAIL
                    notes.append(f"nginx is active but port {port} is not listening")
            elif not ours:
                status = FAIL
                owner = ports.owner(self.runner, port, ls)
                notes.append(f"port {port} is used by {owner}. {ports.advice(owner)}")
        if self.cfg.mysql_port in ls and self.services.status(["mysql"]).get("mysql") != "active":
            status = WARN if status == OK else status
            notes.append(f"port {self.cfg.mysql_port} is used by {ports.owner(self.runner, self.cfg.mysql_port, ls)}")
        return Check("ports", f"Ports {self.cfg.http_port}/{self.cfg.https_port}" + ("" if notes else " are free or used by the PIPanel Nginx"),
                     status, "; ".join(notes))

    def units(self) -> Check:
        names = self.services.core_services(self.cfg)
        st = self.services.status(names)
        down = [n for n, s in st.items() if s != "active"]
        if down:
            return Check("units", "PIPanel services are not running", WARN, ", ".join(down), fix="start_all")
        return Check("units", "All PIPanel services are running", OK)

    def dns(self) -> Check:
        if not self.query("pipanel-check.test", port=self.cfg.dns_port):
            return Check("dns", "dnsmasq is not answering", FAIL, f"port {self.cfg.dns_port}", fix="start_all")
        if not self.resolved_dropin.exists():
            return Check("dns", "systemd-resolved drop-in is missing", FAIL, str(self.resolved_dropin), fix="setup")
        if not self.sys_resolves("pipanel-check.test"):
            return Check("dns", ".test does not resolve through the system", FAIL,
                         "Is systemd-resolved using the drop-in? Try: sudo systemctl restart systemd-resolved")
        return Check("dns", "The .test domain resolves", OK)

    def ca(self) -> Check:
        if not self.certs.ca_exists():
            return Check("ca", "mkcert CA has not been created", WARN, fix="install_ca")
        if not self.certs.system_trusted():
            return Check("ca", "CA is not trusted by the system (curl/PHP)", WARN, "Browsers trust it, the CLI does not", fix="install_ca")
        return Check("ca", "Certificates are trusted", OK)

    def sockets(self, versions: set[str]) -> list[Check]:
        out = []
        for v in sorted(versions | {self.cfg.default_php}):
            if v not in self.php.installed():
                out.append(Check(f"php{v}", f"PHP {v} is not installed", WARN, fix=f"install_php:{v}"))
            elif not self.paths.php_socket(v).exists():
                out.append(Check(f"php{v}", f"PHP-FPM {v} socket is missing", WARN, fix="start_all"))
        return out
