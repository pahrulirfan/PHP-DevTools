"""Render nginx / php-fpm / dnsmasq / systemd config from the templates/ folder."""
from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError

from .paths import Paths

_env = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "templates"),
    undefined=StrictUndefined,   # a variable that was not filled in is an error, not a silently broken config
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)

class TemplateMismatch(Exception):
    pass


def _render(template: str, /, **values) -> str:
    try:
        return _env.get_template(template).render(**values)
    except UndefinedError as e:
        # the template files are read from disk on every render, the code was loaded at start: after an update
        # (git pull, ./install.sh) a running PIPanel no longer matches its templates
        raise TemplateMismatch(f"PIPanel was updated while it was running ({template}: {e}). Quit PIPanel from the "
                               "tray menu and open it again.") from e


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
    return _render("nginx.conf.j2",
        etc_nginx=etc_nginx, http_port=http_port,
        run_dir=paths.run,
        temp_dir=paths.run / "nginx-tmp",
        sites_dir=paths.nginx_sites,
    )


def render_site(paths: Paths, *, name: str, docroot: Path | str, php_version: str,
                tld: str = "test", https: bool = False, upload_max: str = "128m",
                nginx_version: str = "1.24.0", etc_nginx: Path | str = "/etc/nginx",
                http_port: int = 80, https_port: int = 443, local_only: bool = False) -> str:
    if not valid_site_name(name):
        raise ValueError(f"invalid site name: {name!r}")
    if not valid_php_version(php_version):
        raise ValueError(f"invalid PHP version: {php_version!r}")
    return _render("site.conf.j2",
        name=name, tld=tld, docroot=docroot, https=https, upload_max=upload_max, etc_nginx=etc_nginx,
        http_port=http_port, https_port=https_port, local_only=local_only,
        cert=paths.certs / f"{name}.{tld}.pem",
        key=paths.certs / f"{name}.{tld}-key.pem",
        log_dir=paths.logs,
        php_socket=paths.php_socket(php_version),
        http2_directive=nginx_supports_http2_directive(nginx_version),
    )


def render_proxy(paths: Paths, *, name: str, target: str, tld: str = "test", https: bool = False,
                 local_only: bool = False, upload_max: str = "128m", nginx_version: str = "1.24.0",
                 http_port: int = 80, https_port: int = 443) -> str:
    from .sites import normalize_target
    if not valid_site_name(name):
        raise ValueError(f"invalid site name: {name!r}")
    if normalize_target(target) != target:
        raise ValueError(f"invalid proxy target: {target!r}")
    return _render("proxy.conf.j2",
        name=name, tld=tld, target=target, https=https, local_only=local_only, upload_max=upload_max,
        http_port=http_port, https_port=https_port,
        cert=paths.certs / f"{name}.{tld}.pem",
        key=paths.certs / f"{name}.{tld}-key.pem",
        log_dir=paths.logs,
        http2_directive=nginx_supports_http2_directive(nginx_version),
    )


def render_php_fpm(paths: Paths, version: str) -> str:
    if not valid_php_version(version):
        raise ValueError(f"invalid PHP version: {version!r}")
    return _render("php-fpm.conf.j2",
        version=version, run_dir=paths.run, log_dir=paths.logs,
        socket=paths.php_socket(version),
    )


def render_dnsmasq(paths: Paths, *, port: int = 5354, tld: str = "test") -> str:
    return _render("dnsmasq.conf.j2", port=port, tld=tld, run_dir=paths.run)


def render_resolved_dropin(*, port: int = 5354, tld: str = "test") -> str:
    return _render("resolved.conf.j2", port=port, tld=tld)


def render_unit(*, description: str, exec_start: str, exec_reload: str | None = None,
                env: dict[str, str] | None = None, exec_pre: list[str] | None = None,
                workdir: Path | str | None = None) -> str:
    return _render("service.service.j2",
        description=description, exec_start=exec_start, exec_reload=exec_reload, env=env or {},
        exec_pre=exec_pre or [], workdir=workdir,
    )


def render_mysql_cnf(*, datadir, socket, pid, log, port: int = 3306, engine: str = "mysql") -> str:
    return _render("mysql.cnf.j2", datadir=datadir, socket=socket, pid=pid, log=log, port=port,
                                                    engine=engine)


def render_redis_conf(paths: Paths, *, port: int = 6379) -> str:
    return _render("redis.conf.j2", port=port, run_dir=paths.run, data_dir=paths.data / "redis")
