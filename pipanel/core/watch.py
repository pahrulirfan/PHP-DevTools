"""New project folders become sites on their own, like Herd: a systemd path unit watches the projects folders
(inotify, no polling) and runs `pipanel sync --settle`, also while the app is closed.

DNS needs nothing (dnsmasq already answers every *.test name); only the nginx config of the new site is written.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

from .sites import PLAIN, Site

SERVICE = "watch"   # pipanel-watch.path starts pipanel-watch.service
QUIET = 20.0        # stop after this long without changes...
YOUNG = 180.0       # ...unless a folder was created this recently: `laravel new` and `git clone` keep filling it
STEP = 3.0
LIMIT = 600.0


def systemd_escape(value: str) -> str:
    """'%' starts a specifier in unit files."""
    return value.replace("%", "%%")


def render_path_unit(folders: list[str], description: str = "PIPanel: watch the projects folders") -> str:
    lines = ["[Unit]", f"Description={description}", "", "[Path]"]
    lines += [f"PathChanged={systemd_escape(f)}" for f in folders]
    lines += ["", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def sync_env(config: Path, data: Path, state: Path, run: Path) -> dict[str, str]:
    """Environment for the sync service, so it finds exactly the folders of the app that wrote it."""
    env = {"XDG_CONFIG_HOME": str(config.parent), "XDG_DATA_HOME": str(data.parent),
           "XDG_STATE_HOME": str(state.parent), "XDG_RUNTIME_DIR": str(run.parent)}
    if os.environ.get("PIPANEL_UNIT_PREFIX"):
        env["PIPANEL_UNIT_PREFIX"] = os.environ["PIPANEL_UNIT_PREFIX"]
    return env


def signature(sites: list[Site]) -> dict[str, tuple[str, str, str]]:
    """What the nginx config depends on: when this changes, the config must be written again."""
    return {s.name: (str(s.path), s.kind, str(s.docroot)) for s in sites}


def still_filling(site: Site, now: float, young: float = YOUNG) -> bool:
    """A folder created moments ago whose project type is not known yet (files are still arriving)."""
    try:
        recent = now - site.path.stat().st_mtime < young
    except OSError:
        return False
    return recent and site.kind == PLAIN


def settle(sites: Callable[[], list[Site]], apply: Callable[[], object], *, quiet: float = QUIET,
           young: float = YOUNG, step: float = STEP, limit: float = LIMIT,
           clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
           sleep: Callable[[float], None] = time.sleep) -> int:
    """Apply now, then keep rescanning while folders are still changing. Returns how many times it applied.

    The path unit does not start the service again while it runs, so everything that happens meanwhile is
    picked up here."""
    current = sites()
    last = signature(current)
    apply()
    applied = 1
    start = clock()
    quiet_until = start + quiet
    while clock() - start < limit:
        if clock() >= quiet_until and not any(still_filling(s, wall(), young) for s in current):
            break
        sleep(step)
        current = sites()
        sig = signature(current)
        if sig != last:
            apply()
            applied += 1
            last = sig
            quiet_until = clock() + quiet
    return applied
