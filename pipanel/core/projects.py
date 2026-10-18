"""Create a new Laravel project and configure its .env."""
from __future__ import annotations

import re
from pathlib import Path

from .paths import Paths
from .runner import Result, Runner

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ProjectError(Exception):
    pass


def dotenv_value(v: str) -> str:
    if v == "" or not re.search(r"[\s#\"'\\$=]", v):
        return v
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def create_laravel(runner: Runner, paths: Paths, parent: Path, name: str) -> Path:
    if not _NAME.match(name):
        raise ProjectError(f"invalid project name: {name!r}")
    target = parent / name
    if target.exists():
        raise ProjectError(f"{target} already exists")
    composer = paths.bin / "composer"
    if not composer.exists():
        raise ProjectError("Composer is not installed")
    r = runner.run([str(composer), "create-project", "laravel/laravel", name, "--no-interaction", "--prefer-dist"],
                   cwd=str(parent), env={"PATH": f"{paths.bin}:/usr/local/bin:/usr/bin:/bin"}, timeout=1800)
    if not r.ok:
        raise ProjectError(r.text)
    return target


def set_env(project: Path, values: dict[str, str]) -> None:
    """Update/add variables in .env (copied from .env.example if needed)."""
    env = project / ".env"
    if not env.exists():
        ex = project / ".env.example"
        if not ex.exists():
            raise ProjectError(".env not found")
        env.write_text(ex.read_text())
    text = env.read_text()
    for k, v in values.items():
        line = f"{k}={dotenv_value(v)}"
        pat = re.compile(rf"^#?\s*{re.escape(k)}=.*$", re.M)
        text = pat.sub(lambda _m, l=line: l, text, count=1) if pat.search(text) else text.rstrip("\n") + "\n" + line + "\n"
    env.write_text(text)


def dotenv_get(project: Path, key: str) -> str | None:
    """Value of `key` in the project's .env (quotes removed), or None when it is not set."""
    try:
        text = (project / ".env").read_text()
    except OSError:
        return None
    m = re.search(rf"^{re.escape(key)}=(.*)$", text, re.M)
    if not m:
        return None
    v = m.group(1).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


# What a Laravel project cloned from Git usually lacks (none of it is committed)
NEEDS = {
    "vendor": "the vendor folder is missing (composer install was not run)",
    "env": "there is no .env file",
    "key": "APP_KEY in .env is empty",
}
_SKIP_DIRS = {"vendor", "node_modules", ".git", "storage"}


def setup_needs(project: Path) -> list[str]:
    """Keys of NEEDS that apply to a Laravel project, in the order they are fixed."""
    out = []
    if (project / "composer.json").is_file() and not (project / "vendor" / "autoload.php").is_file():
        out.append("vendor")
    if not (project / ".env").is_file():
        out.append("env")
    elif not dotenv_get(project, "APP_KEY"):
        out.append("key")
    return out


def sql_dumps(project: Path, limit: int = 20) -> list[Path]:
    """SQL dumps shipped in the repository (.sql / .sql.gz), newest first."""
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for e in entries:
            if e.is_dir() and not e.is_symlink() and e.name not in _SKIP_DIRS and depth < 4:
                walk(e, depth + 1)
            elif e.is_file() and (e.name.endswith(".sql") or e.name.endswith(".sql.gz")):
                found.append(e)

    walk(project, 0)
    return sorted(found, key=lambda f: f.stat().st_mtime, reverse=True)[:limit]


def mysql_env(port: int, database: str, password: str) -> dict[str, str]:
    return {"DB_CONNECTION": "mysql", "DB_HOST": "127.0.0.1", "DB_PORT": str(port),
            "DB_DATABASE": database, "DB_USERNAME": "root", "DB_PASSWORD": password}
