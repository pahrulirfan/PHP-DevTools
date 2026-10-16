"""Calls the root helper through pkexec. Fixed subcommands only; the helper validates every argument."""
from __future__ import annotations

from pathlib import Path

from .runner import Result, Runner

HELPER = Path("/usr/lib/pipanel/pipanel-helper")


class Privileged:
    def __init__(self, runner: Runner, helper: Path = HELPER):
        self.runner, self.helper = runner, helper

    def available(self) -> bool:
        return self.helper.exists()

    def call(self, *args: str) -> Result:
        if not self.available():
            return Result(127, "", f"helper is not installed at {self.helper}. Run ./install.sh")
        return self.runner.run(["pkexec", str(self.helper), *args], timeout=1800)

    def setup(self, default_php: str = "8.3") -> Result: return self.call("setup", default_php)
    def install_php(self, v: str) -> Result: return self.call("install-php", v)
    def remove_php(self, v: str) -> Result: return self.call("remove-php", v)
    def install_ext(self, v: str, name: str) -> Result: return self.call("install-ext", v, name)
    def install_database(self, engine: str) -> Result: return self.call("install-database", engine)
    def trust_ca(self, path: Path) -> Result: return self.call("trust-ca", str(path))
    def fix_system_services(self) -> Result: return self.call("disable-system-services")
