"""Mailpit: catches every email the local apps send and shows it in a web inbox (mailpit.test)."""
from __future__ import annotations

import tarfile
from pathlib import Path
from typing import Callable

from .ngrok import arch
from .paths import Paths

URL = "https://github.com/axllent/mailpit/releases/latest/download/mailpit-{os}-{arch}.{ext}"
SMTP_PORT, UI_PORT = 1025, 8025
SITE = "mailpit"   # served as mailpit.test (this computer only)


class MailpitError(Exception):
    pass


class Mailpit:
    def __init__(self, paths: Paths):
        self.paths = paths

    @property
    def bin(self) -> Path:
        from . import platform
        return self.paths.bin / platform.exe("mailpit")

    def installed(self) -> bool:
        return self.bin.exists()

    def install(self, fetch: Callable[[str], bytes], machine: str | None = None) -> Path:
        from . import platform
        from .archive import ArchiveError, member
        os_name = {platform.WINDOWS: "windows", platform.MACOS: "darwin"}.get(platform.current(), "linux")
        data = fetch(URL.format(os=os_name, arch=arch(machine), ext="zip" if platform.is_windows() else "tar.gz"))
        try:
            binary = member(data, self.bin.name)
        except (ArchiveError, tarfile.TarError) as e:
            raise MailpitError("archive does not contain the mailpit binary") from e
        self.paths.bin.mkdir(parents=True, exist_ok=True)
        self.bin.write_bytes(binary)
        self.bin.chmod(0o755)
        return self.bin

    def unit_spec(self) -> dict:
        db = self.paths.data / "mailpit.db"
        return dict(description="PIPanel: Mailpit",
                    argv=[str(self.bin), "--smtp", f"127.0.0.1:{SMTP_PORT}", "--listen", f"127.0.0.1:{UI_PORT}",
                          "--database", str(db), "--smtp-auth-accept-any", "--smtp-auth-allow-insecure"],
                    dirs=[str(self.paths.data)])

    @staticmethod
    def env() -> dict[str, str]:
        """Laravel .env values that send mail to Mailpit."""
        return {"MAIL_MAILER": "smtp", "MAIL_HOST": "127.0.0.1", "MAIL_PORT": str(SMTP_PORT)}
