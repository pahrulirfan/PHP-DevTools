"""phpMyAdmin, served as phpmyadmin.test to this computer only and signed in as root automatically."""
from __future__ import annotations

import hashlib
import io
import secrets
import shutil
import zipfile
from pathlib import Path
from typing import Callable

from .adminer import PASSWORD_PHP, php_str
from .paths import Paths

URL = "https://www.phpmyadmin.net/downloads/phpMyAdmin-latest-english.zip"
SITE = "phpmyadmin"
MAX_PHP = (8, 3)   # phpMyAdmin 5.2 supports PHP < 8.4

CONFIG = """<?php
// Managed by PIPanel. Do not edit manually. The site only answers 127.0.0.1 (see its nginx config).
""" + PASSWORD_PHP + """
$cfg['blowfish_secret'] = %(secret)s;
$i = 1;
$cfg['Servers'][$i]['auth_type'] = 'config';
$cfg['Servers'][$i]['host'] = '127.0.0.1';
$cfg['Servers'][$i]['port'] = %(port)s;
$cfg['Servers'][$i]['user'] = 'root';
$cfg['Servers'][$i]['password'] = pipanel_password(%(client_cnf)s);
$cfg['Servers'][$i]['AllowNoPassword'] = true;
$cfg['TempDir'] = %(tmp)s;
$cfg['VersionCheck'] = false;
$cfg['SendErrorReports'] = 'never';
$cfg['ShowPhpInfo'] = false;
"""


class PhpMyAdminError(Exception):
    pass


def pick_php(installed: list[str], default: str) -> str:
    """The newest installed PHP that phpMyAdmin supports (the default when none is old enough)."""
    ok = [v for v in installed if tuple(map(int, v.split("."))) <= MAX_PHP]
    return ok[-1] if ok else default


class PhpMyAdmin:
    def __init__(self, paths: Paths):
        self.paths = paths

    @property
    def root(self) -> Path:
        return self.paths.data / "phpmyadmin"

    def installed(self) -> bool:
        return (self.root / "index.php").exists()

    def install(self, fetch: Callable[[str], bytes]) -> Path:
        data = fetch(URL)
        want = fetch(URL + ".sha256").decode().split()[0].lower()
        if hashlib.sha256(data).hexdigest() != want:
            raise PhpMyAdminError("phpMyAdmin download checksum mismatch")
        new = self.root.with_name("phpmyadmin.new")
        shutil.rmtree(new, ignore_errors=True)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.infolist():
                parts = Path(m.filename).parts[1:]   # drop the phpMyAdmin-5.x-english/ folder
                if not parts or m.is_dir():
                    continue
                dest = new.joinpath(*parts)
                if ".." in parts or not dest.resolve().is_relative_to(new.resolve()):
                    raise PhpMyAdminError(f"unsafe path in the archive: {m.filename}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(m))
        if not (new / "index.php").exists():
            shutil.rmtree(new, ignore_errors=True)
            raise PhpMyAdminError("the download is not phpMyAdmin")
        shutil.rmtree(self.root, ignore_errors=True)
        new.rename(self.root)
        return self.root

    def write_config(self, client_cnf: Path, port: int) -> bool:
        """config.inc.php: signs in with the root password stored in the database's client.cnf."""
        secret_file = self.paths.data / "phpmyadmin-secret"
        if not secret_file.exists():
            secret_file.write_text(secrets.token_hex(16))   # 32 characters, as phpMyAdmin requires
            secret_file.chmod(0o600)
        tmp = self.paths.data / "phpmyadmin-tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        text = CONFIG % {"secret": php_str(secret_file.read_text().strip()), "port": php_str(str(port)),
                         "client_cnf": php_str(str(client_cnf)), "tmp": php_str(str(tmp))}
        f = self.root / "config.inc.php"
        if f.exists() and f.read_text() == text:
            return False
        f.write_text(text)
        return True
