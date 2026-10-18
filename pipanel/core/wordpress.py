"""New WordPress sites through WP-CLI, run with the site's PHP version. Passwords go through stdin (--prompt),
never on a command line."""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import Paths
from .php import PHP
from .runner import Result, Runner

PHAR_URL = "https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class WordPressError(Exception):
    pass


@dataclass
class NewSite:
    path: Path
    url: str
    admin_user: str
    admin_password: str
    database: str


class WordPress:
    def __init__(self, paths: Paths, runner: Runner, php: PHP):
        self.paths, self.runner, self.php = paths, runner, php

    @property
    def phar(self) -> Path:
        return self.paths.bin / "wp-cli.phar"

    def installed(self) -> bool:
        return self.phar.exists()

    def install(self, fetch: Callable[[str], bytes]) -> Path:
        """Download WP-CLI and verify its SHA-512; `wp` in the terminal runs it with PIPanel's PHP."""
        phar = fetch(PHAR_URL)
        want = fetch(PHAR_URL + ".sha512").decode().split()[0].lower()
        if hashlib.sha512(phar).hexdigest() != want:
            raise WordPressError("wp-cli.phar checksum mismatch")
        self.paths.bin.mkdir(parents=True, exist_ok=True)
        self.phar.write_bytes(phar)
        w = self.paths.bin / "wp"
        w.write_text('#!/bin/sh\nexec "$(dirname "$0")/php" "$(dirname "$0")/wp-cli.phar" "$@"\n')
        w.chmod(0o755)
        return w

    def wp(self, project: Path, php_version: str, *args: str, stdin: str | None = None,
           timeout: float = 900) -> Result:
        argv, env = self.php.cli_cmd(php_version, [str(self.phar), *args, f"--path={project}"])
        return self.runner.run(argv, cwd=str(project), env=env, input=stdin, timeout=timeout)

    def create(self, parent: Path, name: str, *, php_version: str, url: str, db_name: str, db_port: int,
               db_password: str) -> NewSite:
        """Download WordPress, write wp-config.php for the local database, and run the installer."""
        if not _NAME.match(name):
            raise WordPressError(f"invalid project name: {name!r}")
        if not self.installed():
            raise WordPressError("WP-CLI is not installed")
        target = parent / name
        if target.exists():
            raise WordPressError(f"{target} already exists")
        target.mkdir(parents=True)

        def step(r: Result, what: str) -> None:
            if not r.ok:
                raise WordPressError(f"{what} failed: {r.text[-600:]}")

        step(self.wp(target, php_version, "core", "download"), "Downloading WordPress")
        step(self.wp(target, php_version, "config", "create", f"--dbname={db_name}", "--dbuser=root",
                     f"--dbhost=127.0.0.1:{db_port}", "--prompt=dbpass", stdin=db_password + "\n"),
             "Writing wp-config.php")
        password = secrets.token_urlsafe(12)
        step(self.wp(target, php_version, "core", "install", f"--url={url}", f"--title={name}",
                     "--admin_user=admin", f"--admin_email=admin@{name.lower()}.test", "--skip-email",
                     "--prompt=admin_password", stdin=password + "\n"),
             "Installing WordPress")
        return NewSite(target, url, "admin", password, db_name)
