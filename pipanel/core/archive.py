"""Unpack downloaded archives safely (no path may leave the target folder) and replace the target in one step."""
from __future__ import annotations

import io
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path


class ArchiveError(Exception):
    pass


def _target(dest: Path, name: str, strip: int) -> Path | None:
    parts = [p for p in Path(name.replace("\\", "/")).parts if p not in ("", ".")][strip:]
    if not parts:
        return None
    if ".." in parts or Path(parts[0]).is_absolute() or ":" in parts[0]:
        raise ArchiveError(f"unsafe path in the archive: {name}")
    return dest.joinpath(*parts)


def unpack(data: bytes, dest: Path, *, strip: int = 0, kind: str | None = None) -> Path:
    """Unpack a .zip or .tar.gz into `dest` (replacing it). `strip` drops leading folders, like tar's option."""
    kind = kind or ("zip" if data[:2] == b"PK" else "tar")
    new = dest.with_name(dest.name + ".new")
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir(parents=True)
    if kind == "zip":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.infolist():
                t = _target(new, m.filename, strip)
                if t is None or m.is_dir():
                    continue
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_bytes(z.read(m))
                mode = (m.external_attr >> 16) & 0o777
                if mode & 0o111:
                    t.chmod(t.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for m in tf.getmembers():
                t = _target(new, m.name, strip)
                if t is None or not (m.isfile() or m.isdir()):
                    continue
                if m.isdir():
                    t.mkdir(parents=True, exist_ok=True)
                    continue
                t.parent.mkdir(parents=True, exist_ok=True)
                f = tf.extractfile(m)
                assert f is not None
                t.write_bytes(f.read())
                if m.mode & 0o111:
                    t.chmod(0o755)
    shutil.rmtree(dest, ignore_errors=True)
    new.rename(dest)
    return dest


def member(data: bytes, name: str) -> bytes:
    """One file of a .zip or .tar.gz, found by its base name."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.infolist():
                if Path(m.filename.replace("\\", "/")).name == name and not m.is_dir():
                    return z.read(m)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for m in tf.getmembers():
                if m.isfile() and Path(m.name).name == name:
                    f = tf.extractfile(m)
                    assert f is not None
                    return f.read()
    raise ArchiveError(f"{name} is not in the archive")
