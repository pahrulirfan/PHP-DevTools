"""Adminer: a one-file database manager, served as adminer.test to this computer only, signed in automatically."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .paths import Paths

URL = "https://www.adminer.org/latest-mysql-en.php"
SITE = "adminer"

# Reads the root password from the database's client.cnf on every request (a changed password needs no rewrite).
# client.cnf escapes \ and " with a backslash.
PASSWORD_PHP = r"""
function pipanel_password($file) {
    $text = @file_get_contents($file);
    if ($text === false || !preg_match('/^password="(.*)"\r?$/m', $text, $m)) {
        return '';
    }
    return strtr($m[1], array('\\"' => '"', '\\\\' => '\\'));
}
"""

# Adminer refuses an empty password and asks for a login token; neither helps on a page that only answers
# 127.0.0.1 (see the nginx allow/deny in the site config), so this wrapper signs in with the root account.
INDEX = r"""<?php
// Managed by PIPanel. Do not edit manually.
function adminer_object() {
    class PIPanelAdminer extends \Adminer\Adminer {
        function name() { return 'PIPanel'; }
        function login($login, $password) { return true; }
        function verifyLoginToken() { return false; }
    }
    return new PIPanelAdminer;
}
""" + PASSWORD_PHP + r"""
if (!isset($_GET['username']) && !isset($_POST['auth']) && !isset($_GET['file'])) {
    $_POST['auth'] = array('driver' => 'server', 'server' => %(server)s, 'username' => 'root',
                           'password' => pipanel_password(%(client_cnf)s), 'db' => '');
}
include __DIR__ . '/adminer.php';
"""


def php_str(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


class AdminerError(Exception):
    pass


class Adminer:
    def __init__(self, paths: Paths):
        self.paths = paths

    @property
    def root(self) -> Path:
        return self.paths.data / "adminer"

    def installed(self) -> bool:
        return (self.root / "adminer.php").exists()

    def install(self, fetch: Callable[[str], bytes]) -> Path:
        data = fetch(URL)
        if not data.startswith(b"<?php") or b"Adminer" not in data[:2000]:
            raise AdminerError("the download is not Adminer")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "adminer.php").write_bytes(data)
        return self.root

    def write_index(self, client_cnf: Path, port: int) -> bool:
        """The sign-in wrapper; rewritten when the database engine or port changes."""
        text = INDEX % {"client_cnf": php_str(str(client_cnf)), "server": php_str(f"127.0.0.1:{port}")}
        f = self.root / "index.php"
        if f.exists() and f.read_text() == text:
            return False
        self.root.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return True
