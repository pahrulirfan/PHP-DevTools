"""XDG-based config/data/run locations. All paths go through here so tests can use a temp folder."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP = "pipanel"


@dataclass(frozen=True)
class Paths:
    config: Path   # ~/.config/pipanel
    data: Path     # ~/.local/share/pipanel
    state: Path    # ~/.local/state/pipanel
    run: Path      # $XDG_RUNTIME_DIR/pipanel
    systemd_user: Path  # ~/.config/systemd/user

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, system: str | None = None) -> "Paths":
        from . import platform
        env = os.environ if env is None else env
        system = system or platform.current()
        if system == platform.WINDOWS:   # %LOCALAPPDATA%\PIPanel\{config,data,state,run}
            base = Path(env.get("PIPANEL_HOME") or Path(env.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "PIPanel")
            return cls.under(base, systemd_user=base / "units")
        if system == platform.MACOS:     # no spaces: socket paths go into nginx and PHP-FPM configs
            base = Path(env.get("PIPANEL_HOME") or Path(env.get("HOME") or Path.home()) / "Library" / "PIPanel")
            return cls.under(base, systemd_user=base / "units")
        home = Path(env.get("HOME") or Path.home())

        def xdg(var: str, default: str) -> Path:
            return Path(env.get(var) or home / default)

        runtime = env.get("XDG_RUNTIME_DIR")
        config_home = xdg("XDG_CONFIG_HOME", ".config")
        return cls(
            config=config_home / APP,
            data=xdg("XDG_DATA_HOME", ".local/share") / APP,
            state=xdg("XDG_STATE_HOME", ".local/state") / APP,
            run=(Path(runtime) if runtime else Path(f"/run/user/{os.getuid()}")) / APP,
            systemd_user=config_home / "systemd" / "user",
        )

    @classmethod
    def under(cls, root: Path, systemd_user: Path | None = None) -> "Paths":
        """All paths under a single folder (tests, Windows and macOS)."""
        return cls(
            config=root / "config",
            data=root / "data",
            state=root / "state",
            run=root / "run",
            systemd_user=systemd_user or root / "systemd-user",
        )

    @property
    def config_file(self) -> Path:
        return self.config / "config.json"

    @property
    def nginx_dir(self) -> Path:
        return self.config / "nginx"

    @property
    def nginx_sites(self) -> Path:
        return self.nginx_dir / "sites"

    @property
    def dns_dir(self) -> Path:
        return self.config / "dns"

    @property
    def certs(self) -> Path:
        return self.config / "certs"

    @property
    def logs(self) -> Path:
        return self.state / "logs"

    @property
    def bin(self) -> Path:
        return self.data / "bin"

    def db_data(self, engine: str) -> Path:
        """Data folder of a database engine. Each engine keeps its own: the formats are not compatible."""
        return self.data / engine

    @property
    def mysql_data(self) -> Path:
        return self.db_data("mysql")

    def php_dir(self, version: str) -> Path:
        return self.config / "php" / version

    def php_socket(self, version: str) -> Path:
        return self.run / f"php{version}.sock"

    def ensure(self) -> None:
        for p in (self.config, self.data, self.state, self.run, self.systemd_user,
                  self.nginx_sites, self.dns_dir, self.certs, self.logs, self.bin):
            p.mkdir(parents=True, exist_ok=True)
