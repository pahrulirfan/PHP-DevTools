"""mkcert: local CA and per-site certificates."""
from __future__ import annotations

from pathlib import Path

from .paths import Paths
from .runner import Result, Runner
from .templates import valid_site_name

SYSTEM_CA = Path("/usr/local/share/ca-certificates/pipanel-mkcert.crt")


class CertError(Exception):
    pass


class Certs:
    def __init__(self, paths: Paths, runner: Runner, *, system_ca: Path = SYSTEM_CA):
        self.paths, self.runner, self.system_ca = paths, runner, system_ca

    def available(self) -> bool:
        """mkcert is optional: it is only needed for HTTPS. Plain http://name.test works without it."""
        import shutil
        return shutil.which("mkcert") is not None

    def caroot(self) -> Path | None:
        r = self.runner.run(["mkcert", "-CAROOT"])
        return Path(r.out.strip()) if r.ok and r.out.strip() else None

    def ca_exists(self) -> bool:
        root = self.caroot()
        return bool(root and (root / "rootCA.pem").exists())

    def install_ca_user(self) -> Result:
        """Install the CA into the NSS store (Firefox/Chrome) without root. The system store goes through the `trust-ca` helper command."""
        return self.runner.run(["mkcert", "-install"], env={"TRUST_STORES": "nss"})

    def system_trusted(self) -> bool:
        return self.system_ca.exists()

    def cert_paths(self, name: str, tld: str) -> tuple[Path, Path]:
        return (self.paths.certs / f"{name}.{tld}.pem", self.paths.certs / f"{name}.{tld}-key.pem")

    def has_cert(self, name: str, tld: str) -> bool:
        return all(p.exists() for p in self.cert_paths(name, tld))

    def ensure_site_cert(self, name: str, tld: str = "test") -> Path:
        if not valid_site_name(name):
            raise CertError(f"invalid site name: {name!r}")
        cert, key = self.cert_paths(name, tld)
        if cert.exists() and key.exists():
            return cert
        self.paths.certs.mkdir(parents=True, exist_ok=True)
        r = self.runner.run(["mkcert", "-cert-file", str(cert), "-key-file", str(key),
                             f"{name}.{tld}", f"*.{name}.{tld}"])
        if r.code == 127:   # command not found
            raise CertError("HTTPS needs mkcert, which is not installed. Install it with `sudo apt install mkcert` "
                            "(or Services -> Doctor -> Fix). Sites keep working over plain http:// meanwhile.")
        if not r.ok or not cert.exists():
            raise CertError(f"mkcert failed: {r.text}")
        key.chmod(0o600)
        return cert

    def remove_site_cert(self, name: str, tld: str = "test") -> None:
        for p in self.cert_paths(name, tld):
            p.unlink(missing_ok=True)
