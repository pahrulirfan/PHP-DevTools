"""Render nginx / php-fpm / dnsmasq / systemd config from the templates/ folder."""
from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .paths import Paths

_env = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "templates"),
    undefined=StrictUndefined,   # a variable that was not filled in is an error, not a silently broken config
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)

_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_VERSION_RE = re.compile(r"^8\.[0-9]$")


def valid_site_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


def valid_php_version(v: str) -> bool:
    return bool(_VERSION_RE.match(v))


def nginx_supports_http2_directive(version: str) -> bool:
    """`http2 on;` exists since nginx 1.25.1."""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    return bool(m) and tuple(map(int, m.groups())) >= (1, 25, 1)


def render_nginx_conf(paths: Paths, etc_nginx: Path | str = "/etc/nginx", http_port: int = 80) -> str:
    return _env.get_template("nginx.conf.j2").render(
        etc_nginx=etc_nginx, http_port=http_port,
        run_dir=paths.run,
        temp_dir=paths.run / "nginx-tmp",
        sites_dir=paths.nginx_sites,
    )


def render_site(paths: Paths, *, name: str, docroot: Path | str, php_version: str,
                tld: str = "test", https: bool = False, upload_max: str = "128m",
                nginx_version: str = "1.24.0", etc_nginx: Path | str = "/etc/nginx",
                http_port: int = 80, https_port: int = 443) -> str:
    if not valid_site_name(name):
        raise ValueError(f"invalid site name: {name!r}")
    if not valid_php_version(php_version):
        raise ValueError(f"invalid PHP version: {php_version!r}")
    return _env.get_template("site.conf.j2").render(
        name=name, tld=tld, docroot=docroot, https=https, upload_max=upload_max, etc_nginx=etc_nginx,
        http_port=http_port, https_port=https_port,
        cert=paths.certs / f"{name}.{tld}.pem",
        key=paths.certs / f"{name}.{tld}-key.pem",
        log_dir=paths.logs,
        php_socket=paths.php_socket(php_version),
        http2_directive=nginx_supports_http2_directive(nginx_version),
    )


def render_php_fpm(paths: Paths, version: str) -> str:
    if not valid_php_version(version):
        raise ValueError(f"invalid PHP version: {version!r}")
    return _env.get_template("php-fpm.conf.j2").render(
        version=version, run_dir=paths.run, log_dir=paths.logs,
        socket=paths.php_socket(version),
    )


def render_dnsmasq(paths: Paths, *, port: int = 5354, tld: str = "test") -> str:
    return _env.get_template("dnsmasq.conf.j2").render(port=port, tld=tld, run_dir=paths.run)


def render_resolved_dropin(*, port: int = 5354, tld: str = "test") -> str:
    return _env.get_template("resolved.conf.j2").render(port=port, tld=tld)


def render_unit(*, description: str, exec_start: str, exec_reload: str | None = None,
                env: dict[str, str] | None = None, exec_pre: list[str] | None = None) -> str:
    return _env.get_template("service.service.j2").render(
        description=description, exec_start=exec_start, exec_reload=exec_reload, env=env or {},
        exec_pre=exec_pre or [],
    )


def render_mysql_cnf(*, datadir, socket, pid, log, port: int = 3306, engine: str = "mysql") -> str:
    return _env.get_template("mysql.cnf.j2").render(datadir=datadir, socket=socket, pid=pid, log=log, port=port,
                                                    engine=engine)
