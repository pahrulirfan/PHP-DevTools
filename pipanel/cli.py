"""`pipanel` in a terminal: the everyday actions of the app, Valet-style. Without a command it opens the app."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .core import actions
from .core.controller import Controller, ControllerError
from .core.doctor import OK
from .core.services import service_title
from .core.sites import Site

STATE = {"active": "running", "inactive": "stopped", "failed": "ERROR", "not-found": "not created"}


def site_here(ctl: Controller, cwd: Path) -> Site | None:
    """The site whose folder contains `cwd` (the deepest one when sites are nested)."""
    cwd = cwd.resolve()
    best = None
    for s in ctl.sites():
        root = s.path.resolve()
        if (cwd == root or root in cwd.parents) and (best is None or len(str(root)) > len(str(best.path.resolve()))):
            best = s
    return best


def pick_site(ctl: Controller, name: str | None) -> Site:
    if name:
        return ctl.site(name)
    s = site_here(ctl, Path.cwd())
    if s is None:
        raise ControllerError("not inside a site folder; give the site name")
    return s


def cmd_status(ctl, a, out):
    states = ctl.service_states()
    in_use = ctl.php_in_use()
    for name, st in states.items():
        note = "  (no site uses it)" if name.startswith("php") and name[3:] not in in_use and st != "active" else ""
        print(f"{service_title(name, ctl.db_label):32} {STATE.get(st, st)}{note}", file=out)


def cmd_sites(ctl, a, out):
    sites, proxies = ctl.sites(), ctl.proxies()
    if not sites and not proxies:
        print("No sites yet. Run `pipanel park` in the folder that holds your projects.", file=out)
    for s in sites:
        print(f"{ctl.site_url(s):38} PHP {s.php}  {s.path}", file=out)
    for x in proxies:
        print(f"{ctl.site_url(x):38} -> {x.target}", file=out)


def cmd_which(ctl, a, out):
    s = site_here(ctl, Path.cwd())
    if s is None:
        print(f"not in a site: php is the default PHP {ctl.cfg.default_php}", file=out)
    else:
        print(f"{s.name}: {ctl.site_url(s)}  PHP {s.php}  ({s.path})", file=out)


def cmd_park(ctl, a, out):
    folder = Path(a.folder or os.getcwd())
    ctl.park(folder)
    ctl.apply()
    print(f"Every folder in {folder} is now a site.", file=out)


def cmd_link(ctl, a, out):
    folder = Path.cwd()
    name = a.name or folder.name.lower()
    ctl.link(name, folder)
    ctl.apply()
    print(f"{ctl.site_url(ctl.site(name.strip().lower()))} -> {folder}", file=out)


def cmd_unlink(ctl, a, out):
    s = pick_site(ctl, a.name)
    if not s.linked:
        raise ControllerError(f"{s.name} comes from a projects folder; remove the folder or use Settings instead")
    ctl.unlink(s.name)
    ctl.apply()
    print(f"Removed the link {s.name}", file=out)


def _secure(ctl, a, out, on: bool):
    s = pick_site(ctl, a.name)
    for w in ctl.set_https(s.name, on).warnings:
        print(w, file=out)
    print(ctl.site_url(ctl.site(s.name)), file=out)


def cmd_isolate(ctl, a, out):
    s = pick_site(ctl, a.name)
    ctl.set_site_php(s.name, a.version)
    print(f"{s.name} now uses PHP {a.version}", file=out)


def cmd_use(ctl, a, out):
    ctl.set_default_php(a.version)
    print(f"PHP {a.version} is the default", file=out)


def cmd_proxy(ctl, a, out):
    ctl.add_proxy(a.name, a.target)
    x = next(p for p in ctl.proxies() if p.name == a.name.strip().lower())
    print(f"{ctl.site_url(x)} -> {x.target}", file=out)


def cmd_unproxy(ctl, a, out):
    ctl.remove_proxy(a.name)
    print(f"Removed the proxy {a.name}", file=out)


def _control(ctl, a, out, verb: str):
    if not a.service:
        r = ctl.stop_all() if verb in ("stop", "restart") else None
        if verb in ("start", "restart"):
            r = ctl.start_all()
    else:
        r = getattr(ctl, f"{verb}_service")(a.service)
    if not r.ok:
        raise ControllerError(r.text or f"{verb} failed")
    cmd_status(ctl, a, out)


def cmd_open(ctl, a, out):
    s = pick_site(ctl, a.name)
    actions.open_url(ctl.site_url(s))


def cmd_share(ctl, a, out):
    s = pick_site(ctl, a.name)
    ctl.share(s.name)
    import time
    for _ in range(30):
        url = ctl.share_url()
        if url:
            print(url, file=out)
            return
        time.sleep(1)
    print("The tunnel is starting; run `pipanel share-url` in a moment.", file=out)


def cmd_share_url(ctl, a, out):
    print(ctl.share_url() or "not sharing", file=out)


def cmd_unshare(ctl, a, out):
    ctl.stop_share()
    print("Stopped sharing", file=out)


def cmd_sync(ctl, a, out):
    n = ctl.sync(settle=a.settle)
    print(f"{len(ctl.sites())} sites up to date" + (f" (config written {n} times)" if a.settle else ""), file=out)


def cmd_doctor(ctl, a, out):
    bad = 0
    for c in ctl.doctor():
        mark = {"ok": "✓", "warn": "!", "fail": "✗"}[c.status]
        bad += c.status != OK
        print(f"{mark} {c.title}" + (f"  ({c.detail})" if c.detail else ""), file=out)
    return 1 if bad else 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pipanel", description="PIPanel: local PHP development. Without a command, "
                                "the app opens.")
    sub = p.add_subparsers(dest="cmd", metavar="command")

    def add(name, fn, help_, *args):
        sp = sub.add_parser(name, help=help_, description=help_)
        for arg, kw in args:
            sp.add_argument(arg, **kw)
        sp.set_defaults(fn=fn)

    site = ("name", dict(nargs="?", help="site name (default: the site of the current folder)"))
    add("status", cmd_status, "show which services run")
    add("sites", cmd_sites, "list the sites and their URLs")
    add("which", cmd_which, "show the site and PHP version of the current folder")
    add("park", cmd_park, "serve every folder inside a folder as name.test",
        ("folder", dict(nargs="?", help="default: the current folder")))
    add("link", cmd_link, "serve the current folder as name.test", ("name", dict(nargs="?")))
    add("unlink", cmd_unlink, "remove a linked site", site)
    add("secure", lambda c, a, o: _secure(c, a, o, True), "serve a site over HTTPS", site)
    add("unsecure", lambda c, a, o: _secure(c, a, o, False), "serve a site over plain HTTP", site)
    add("isolate", cmd_isolate, "use a PHP version for one site", ("version", dict(help="for example 8.2")), site)
    add("use", cmd_use, "change the default PHP version", ("version", dict(help="for example 8.4")))
    add("proxy", cmd_proxy, "serve another local server as name.test",
        ("name", dict()), ("target", dict(help="a port (3000) or http://127.0.0.1:5173")))
    add("unproxy", cmd_unproxy, "remove a proxy", ("name", dict()))
    svc = ("service", dict(nargs="?", help="nginx, dns, php8.3, mysql, mail, redis (default: all)"))
    add("start", lambda c, a, o: _control(c, a, o, "start"), "start services", svc)
    add("stop", lambda c, a, o: _control(c, a, o, "stop"), "stop services", svc)
    add("restart", lambda c, a, o: _control(c, a, o, "restart"), "restart services", svc)
    add("open", cmd_open, "open a site in the browser", site)
    add("share", cmd_share, "share a site on a public URL", site)
    add("share-url", cmd_share_url, "print the public URL of the shared site")
    add("unshare", cmd_unshare, "stop sharing")
    add("doctor", cmd_doctor, "check the environment")
    add("sync", cmd_sync, "serve new project folders now (the folder watcher runs this)",
        ("--settle", dict(action="store_true", help="keep checking while new folders are still filling")))
    return p


COMMANDS = {"status", "sites", "which", "park", "link", "unlink", "secure", "unsecure", "isolate", "use", "proxy",
            "unproxy", "start", "stop", "restart", "open", "share", "share-url", "unshare", "doctor", "sync"}


# Internal commands, used by PIPanel itself (not listed in --help)
HIDDEN = {"_agent", "_dns", "_fcgi", "_run", "_smoke"}


def run_tool(argv: list[str], cwd: Path | None = None) -> int:
    """`php`, `composer` and `wp` on Windows (the .cmd files call this): the PHP version of the site the current
    folder belongs to, like the shell wrapper on Linux and macOS."""
    import subprocess
    from .core import config as cfgmod
    from .core import stack as stackmod
    from .core.paths import Paths
    from .core.runner import Runner
    if not argv or argv[0] not in ("php", "composer", "wp"):
        print("usage: pipanel _run php|composer|wp [args]", file=sys.stderr)
        return 2
    paths = Paths.from_env()
    cfg = cfgmod.load(paths.config_file)
    php = stackmod.for_platform(paths, Runner()).make_php()
    v = os.environ.get("PIPANEL_PHP") or php.version_for(cwd or Path.cwd(), cfg.default_php)
    if v not in php.installed():
        v = cfg.default_php
    tool = {"php": [], "composer": [str(paths.bin / "composer.phar")], "wp": [str(paths.bin / "wp-cli.phar")]}
    cmd, env = php.cli_cmd(v, [*tool[argv[0]], *argv[1:]])
    try:
        return subprocess.call(cmd, env={**os.environ, **env})
    except OSError as e:
        print(f"pipanel: {e}", file=sys.stderr)
        return 127


def hidden(argv: list[str]) -> int:
    cmd, rest = argv[0], argv[1:]
    if cmd == "_agent":
        from .core import agent
        return agent.main()
    if cmd == "_dns":
        from .core import dnsd
        return dnsd.main(rest)
    if cmd == "_fcgi":
        from .core import fcgipool
        return fcgipool.main(rest)
    if cmd == "_smoke":
        from . import smoke
        return smoke.main()
    return run_tool(rest)


def is_cli(argv: list[str]) -> bool:
    return bool(argv) and (argv[0] in COMMANDS or argv[0] in HIDDEN or argv[0] in ("-h", "--help"))


def main(argv: list[str], ctl: Controller | None = None, out=sys.stdout) -> int:
    if argv and argv[0] in HIDDEN:
        return hidden(argv)
    a = parser().parse_args(argv)
    ctl = ctl or Controller()
    try:
        return a.fn(ctl, a, out) or 0
    except Exception as e:  # noqa: BLE001 - every error is reported as a message, not a traceback
        print(f"pipanel: {e}", file=sys.stderr)
        return 1
