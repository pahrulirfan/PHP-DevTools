"""Calls the root helper through pkexec. Fixed subcommands only; the helper validates every argument."""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .runner import Result, Runner

HELPER = Path("/usr/lib/pipanel/pipanel-helper")
HELPER_VERSION = 2   # must match HELPER_VERSION in packaging/pipanel-helper
_VERSION_RE = re.compile(r"^HELPER_VERSION\s*=\s*(\d+)", re.M)


class Privileged:
    def __init__(self, runner: Runner, helper: Path = HELPER):
        self.runner, self.helper = runner, helper

    def available(self) -> bool:
        return self.helper.exists()

    def version(self) -> int:
        """Version of the installed helper (readable without root); 1 for helpers from before versioning."""
        try:
            m = _VERSION_RE.search(self.helper.read_text())
        except OSError:
            return 0
        return int(m.group(1)) if m else 1

    def outdated(self) -> bool:
        return self.available() and self.version() < HELPER_VERSION

    def call(self, *args: str) -> Result:
        if not self.available():
            return Result(127, "", f"helper is not installed at {self.helper}. Run ./install.sh")
        return self.runner.run(["pkexec", str(self.helper), *args], timeout=1800)

    def setup(self, default_php: str = "8.3") -> Result: return self.call("setup", default_php)
    def install_php(self, v: str) -> Result: return self.call("install-php", v)
    def remove_php(self, v: str) -> Result: return self.call("remove-php", v)
    def install_ext(self, v: str, name: str) -> Result: return self.call("install-ext", v, name)
    def install_database(self, engine: str) -> Result: return self.call("install-database", engine)

    def install_redis(self) -> Result:
        if self.outdated():
            return Result(1, "", "The root helper is older than this version of PIPanel. Run ./install.sh again "
                                 "from the project folder, then try once more.")
        return self.call("install-redis")
    def trust_ca(self, path: Path) -> Result: return self.call("trust-ca", str(path))
    def fix_system_services(self) -> Result: return self.call("disable-system-services")


# ---------------------------------------------------------------- Windows and macOS
class DesktopPrivileged:
    """The few steps that need administrator rights on Windows and macOS. Everything else (PHP, Nginx, the
    databases) is installed as the user, so there is nothing like the Linux helper's package commands."""

    def available(self) -> bool:
        return True

    def outdated(self) -> bool:
        return False

    def version(self) -> int:
        return HELPER_VERSION


class WindowsPrivileged(DesktopPrivileged):
    """Runs packaging/windows/pipanel-helper.ps1 elevated (one UAC prompt)."""

    def __init__(self, runner: Runner, helper: Path | None = None):
        self.runner = runner
        if helper is None:
            import sys
            from . import platform
            base = Path(sys.executable).parent if platform.frozen() else Path(__file__).resolve().parents[2] / "packaging" / "windows"
            helper = base / "pipanel-helper.ps1"
        self.helper = helper

    def call(self, *args: str, elevated: bool = True) -> Result:
        if not self.helper.exists():
            return Result(127, "", f"helper is missing: {self.helper}")
        script = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.helper), *args]
        if not elevated:
            return self.runner.run(["powershell", *script])
        quoted = ",".join("'" + a.replace("'", "''") + "'" for a in script)
        ps = (f"$p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
              f"-ArgumentList {quoted}; exit $p.ExitCode")
        return self.runner.run(["powershell", "-NoProfile", "-Command", ps], timeout=300)

    def setup_dns(self, tld: str = "test", port: int = 53, ca: Path | None = None) -> Result:
        """One UAC prompt: the DNS rule, and the mkcert root certificate when given."""
        return self.call("-Command", "setup-dns", "-Tld", tld, *(["-CaFile", str(ca)] if ca else []))

    def dns_ready(self, tld: str = "test") -> bool:
        return self.call("-Command", "check", "-Tld", tld, elevated=False).ok

    def trust_ca(self, path: Path) -> Result:
        return self.setup_dns(ca=path)


class MacPrivileged(DesktopPrivileged):
    """/etc/resolver/<tld> and the System keychain need an administrator: macOS shows its password dialog."""
    RESOLVER = PurePosixPath("/etc/resolver")

    def __init__(self, runner: Runner):
        self.runner = runner

    def _admin(self, shell: str) -> Result:
        if self.runner.run(["sudo", "-n", "true"]).ok:   # passwordless sudo (CI machines)
            return self.runner.run(["sudo", "-n", "/bin/sh", "-c", shell], timeout=300)
        script = 'do shell script "' + shell.replace("\\", "\\\\").replace('"', '\\"') + '" with administrator privileges'
        return self.runner.run(["osascript", "-e", script], timeout=300)

    def setup_dns(self, tld: str = "test", port: int = 5354, ca: Path | None = None) -> Result:
        """One password prompt: /etc/resolver/<tld>, and the mkcert root certificate when given."""
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", tld) or not 0 < int(port) < 65536:
            return Result(2, "", "invalid domain or port")
        shell = (f"mkdir -p {self.RESOLVER} && printf 'nameserver 127.0.0.1\\nport {int(port)}\\n' > "
                 f"{self.RESOLVER}/{tld}")
        if ca is not None:
            trust = self._trust_cmd(ca)
            if trust is None:
                return Result(2, "", "only mkcert's rootCA.pem can be trusted")
            shell += " && " + trust
        return self._admin(shell)

    @staticmethod
    def _trust_cmd(path: Path) -> str | None:
        path = Path(path).resolve()
        if path.name != "rootCA.pem" or not path.is_file():
            return None
        q = str(path).replace("'", "'\\''")
        return f"security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain '{q}'"

    def dns_ready(self, tld: str = "test") -> bool:
        return Path(str(self.RESOLVER / tld)).exists()

    def trust_ca(self, path: Path) -> Result:
        cmd = self._trust_cmd(path)
        return self._admin(cmd) if cmd else Result(2, "", "only mkcert's rootCA.pem can be trusted")


def for_platform(runner: Runner):
    from . import platform
    if platform.is_windows():
        return WindowsPrivileged(runner)
    if platform.is_macos():
        return MacPrivileged(runner)
    return Privileged(runner)
