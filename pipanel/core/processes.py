"""Long-running helpers of a project (Vite, queue worker, scheduler, Reverb, Horizon) as systemd user units,
so nobody needs four terminals open. They run with the site's own PHP and Node versions."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import platform
from .laravel import declared_packages
from .node import Node
from .php import PHP
from .services import PROCESS_TITLES
from .sites import LARAVEL, Site


@dataclass(frozen=True)
class Process:
    key: str
    title: str
    detail: str      # the command, shown to the user


def npm_dev_script(project: Path) -> bool:
    try:
        scripts = json.loads((project / "package.json").read_text()).get("scripts", {})
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(scripts, dict) and "dev" in scripts


def node_version_file(project: Path) -> bool:
    return any((project / f).is_file() for f in (".node-version", ".nvmrc"))


ARTISAN = {
    "queue": ["queue:listen", "--tries=1"],   # listen reloads the code for every job, which suits development
    "schedule": ["schedule:work"],
    "reverb": ["reverb:start"],
    "horizon": ["horizon"],
}
NEEDS_PACKAGE = {"reverb": "laravel/reverb", "horizon": "laravel/horizon"}


def available(site: Site) -> list[Process]:
    """Processes that make sense for this project, in display order."""
    out = []
    if npm_dev_script(site.path):
        out.append(Process("vite", PROCESS_TITLES["vite"], "npm run dev"))
    if site.kind == LARAVEL:
        declared = declared_packages(site.path)
        for key, args in ARTISAN.items():
            pkg = NEEDS_PACKAGE.get(key)
            if pkg is None or pkg in declared:
                out.append(Process(key, PROCESS_TITLES[key], "php artisan " + " ".join(args)))
    return out


class ProcessError(Exception):
    pass


def unit_spec(site: Site, key: str, php: PHP, node: Node) -> dict:
    description = f"PIPanel: {site.name} {PROCESS_TITLES.get(key, key)}"
    if key == "vite":
        if not node.available():
            raise ProcessError("Node.js is managed with fnm, which is not installed yet (Node page -> Install fnm)")
        using = [] if node_version_file(site.path) else ["--using", "default"]   # fnm reads .nvmrc / .node-version
        return dict(description=description, workdir=str(site.path), env={"FNM_DIR": str(node.fnm_dir)},
                    argv=[str(node.fnm_bin), "exec", *using, "npm.cmd" if platform.is_windows() else "npm", "run", "dev"])
    if key in ARTISAN:
        argv, env = php.cli_cmd(site.php, ["artisan", *ARTISAN[key]])
        return dict(description=description, workdir=str(site.path), env=env, argv=argv)
    raise ProcessError(f"unknown process: {key}")
