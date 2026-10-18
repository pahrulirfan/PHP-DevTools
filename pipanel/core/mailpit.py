"""Mailpit: catches every email the local apps send and shows it in a web inbox (mailpit.test)."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path
from typing import Callable

from .ngrok import arch
from .paths import Paths

URL = "https://github.com/axllent/mailpit/releases/latest/download/mailpit-linux-{arch}.tar.gz"
SMTP_PORT, UI_PORT = 1025, 8025
SITE = "mailpit"   # served as mailpit.test (this computer only)


class MailpitError(Exception):
    pass


class Mailpit:
    def __init__(self, paths: Paths):
        self.paths = paths

    @property
    def bin(self) -> Path:
        return self.paths.bin / "mailpit"

    def installed(self) -> bool:
        return self.bin.exists()

    def install(self, fetch: Callable[[str], bytes], machine: str | None = None) -> Path:
        data = fetch(URL.format(arch=arch(machine)))
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            member = next((m for m in t.getmembers() if m.isfile() and Path(m.name).name == "mailpit"), None)
            if member is None:
                raise MailpitError("archive does not contain the mailpit binary")
            f = t.extractfile(member)
            assert f is not None
            self.paths.bin.mkdir(parents=True, exist_ok=True)
            self.bin.write_bytes(f.read())
        self.bin.chmod(0o755)
        return self.bin

    def unit_spec(self) -> dict:
        db = self.paths.data / "mailpit.db"
        return dict(description="PIPanel: Mailpit",
                    exec_start=f"{self.bin} --smtp 127.0.0.1:{SMTP_PORT} --listen 127.0.0.1:{UI_PORT} "
                               f"--database {db} --smtp-auth-accept-any --smtp-auth-allow-insecure",
                    exec_pre=[f"/bin/mkdir -p {self.paths.data}"])

    @staticmethod
    def env() -> dict[str, str]:
        """Laravel .env values that send mail to Mailpit."""
        return {"MAIL_MAILER": "smtp", "MAIL_HOST": "127.0.0.1", "MAIL_PORT": str(SMTP_PORT)}
