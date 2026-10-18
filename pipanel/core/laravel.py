"""Install Laravel packages ("plugins") into a project with Composer, using that site's PHP version."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .paths import Paths
from .php import PHP
from .runner import Result, Runner


class LaravelError(Exception):
    pass


@dataclass(frozen=True)
class Package:
    key: str
    name: str
    description: str
    packages: tuple[str, ...]            # composer packages to require
    dev: bool = False                    # goes into require-dev
    post: tuple[tuple[str, ...], ...] = ()   # artisan commands to run afterwards (non-interactive only)
    group: str = "Developer tools"


CATALOG: tuple[Package, ...] = (
    Package("debugbar", "Debugbar", "Toolbar with queries, timeline, and request details", ("barryvdh/laravel-debugbar",), True),
    Package("ide-helper", "IDE Helper", "Autocomplete for facades and models in your editor",
            ("barryvdh/laravel-ide-helper",), True),
    Package("telescope", "Telescope", "Inspect requests, jobs, queries, mail, and more",
            ("laravel/telescope",), False, (("telescope:install",),)),
    Package("pint", "Pint", "Opinionated PHP code style fixer", ("laravel/pint",), True),
    Package("larastan", "Larastan", "Static analysis tuned for Laravel", ("larastan/larastan",), True),
    Package("livewire", "Livewire", "Dynamic interfaces written in PHP", ("livewire/livewire",), False,
            group="Authentication & UI"),
    Package("breeze", "Breeze", "Minimal login, registration, and profile scaffolding (Blade)", ("laravel/breeze",),
            True, (("breeze:install", "blade", "--no-interaction"),), "Authentication & UI"),
    Package("sanctum", "Sanctum", "Lightweight API tokens and SPA authentication", ("laravel/sanctum",), False,
            (("vendor:publish", "--provider=Laravel\\Sanctum\\SanctumServiceProvider"),), "Authentication & UI"),
    Package("socialite", "Socialite", "Login with Google, GitHub, and other providers", ("laravel/socialite",), False,
            group="Authentication & UI"),
    Package("permission", "Spatie Permission", "Roles and permissions", ("spatie/laravel-permission",), False,
            (("vendor:publish", "--provider=Spatie\\Permission\\PermissionServiceProvider"),), "Authentication & UI"),
    Package("horizon", "Horizon", "Dashboard and configuration for Redis queues", ("laravel/horizon",), False,
            (("horizon:install",),), "Production"),
    Package("pest", "Pest", "Elegant testing framework", ("pestphp/pest", "pestphp/pest-plugin-laravel"), True,
            (("pest:install", "--no-interaction"),), "Testing"),
)
BY_KEY = {p.key: p for p in CATALOG}

# vendor/package with an optional version constraint, e.g. spatie/laravel-ray:^1.37
_PKG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*(:[A-Za-z0-9^~*.,|<>=@-]{1,30})?$")


def valid_package(spec: str) -> bool:
    return bool(_PKG_RE.match(spec))


def declared_packages(project: Path) -> set[str]:
    """Names listed in composer.json (require and require-dev)."""
    try:
        data = json.loads((project / "composer.json").read_text())
    except (OSError, ValueError):
        return set()
    out: set[str] = set()
    for key in ("require", "require-dev"):
        if isinstance(data.get(key), dict):
            out |= set(data[key])
    return out


def is_installed(pkg: Package, declared: set[str]) -> bool:
    return all(name in declared for name in pkg.packages)


class LaravelTools:
    def __init__(self, paths: Paths, runner: Runner, php: PHP):
        self.paths, self.runner, self.php = paths, runner, php

    # ---- low level ----
    def _run(self, project: Path, php_version: str, args: list[str], timeout: float = 1800) -> Result:
        if not (project / "artisan").is_file():
            raise LaravelError(f"{project} is not a Laravel project (no artisan file)")
        argv, env = self.php.cli_cmd(php_version, args)
        return self.runner.run(argv, cwd=str(project), env=env, timeout=timeout)

    def composer(self, project: Path, php_version: str, *args: str, progress_flag: bool = True) -> Result:
        phar = self.paths.bin / "composer.phar"
        if not phar.exists():
            raise LaravelError("Composer is not installed (PHP page -> Install Composer)")
        extra = ["--no-interaction"] + (["--no-progress"] if progress_flag else [])
        return self._run(project, php_version, [str(phar), *args, *extra])

    def outdated(self, project: Path, php_version: str) -> list[tuple[str, Result]]:
        """Direct dependencies with a newer version."""
        return [("composer outdated --direct",
                 self.composer(project, php_version, "outdated", "--direct", progress_flag=False))]

    def audit(self, project: Path, php_version: str) -> list[tuple[str, Result]]:
        """Known security advisories for the installed packages."""
        return [("composer audit", self.composer(project, php_version, "audit", progress_flag=False))]

    def artisan(self, project: Path, php_version: str, *args: str) -> Result:
        return self._run(project, php_version, ["artisan", *args, "--no-interaction"])

    def migrate(self, project: Path, php_version: str) -> Result:
        """php artisan migrate --force (new projects use database sessions, so the tables must exist)."""
        return self.artisan(project, php_version, "migrate", "--force")

    # ---- operations ----
    def installed(self, project: Path) -> set[str]:
        return {p.key for p in CATALOG if is_installed(p, declared_packages(project))}

    def install(self, project: Path, php_version: str, pkg: Package) -> list[tuple[str, Result]]:
        """composer require, then the package's artisan setup commands. Stops at the first failure."""
        args = ["require", *(["--dev"] if pkg.dev else []), *pkg.packages]
        steps = [(f"composer {' '.join(args)}", self.composer(project, php_version, *args))]
        for cmd in pkg.post:
            if not steps[-1][1].ok:
                break
            steps.append((f"php artisan {' '.join(cmd)}", self.artisan(project, php_version, *cmd)))
        return steps

    def install_custom(self, project: Path, php_version: str, spec: str, dev: bool = False) -> list[tuple[str, Result]]:
        spec = spec.strip()
        if not valid_package(spec):
            raise LaravelError(f"invalid package name: {spec!r} (expected vendor/package or vendor/package:^1.0)")
        args = ["require", *(["--dev"] if dev else []), spec]
        return [(f"composer {' '.join(args)}", self.composer(project, php_version, *args))]

    def remove(self, project: Path, php_version: str, pkg: Package) -> list[tuple[str, Result]]:
        args = ["remove", *pkg.packages]
        return [(f"composer {' '.join(args)}", self.composer(project, php_version, *args))]
