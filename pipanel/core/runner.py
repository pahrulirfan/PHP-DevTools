"""Subprocess wrapper. Other modules take a Runner so they can be tested with a FakeRunner."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


@dataclass
class Result:
    code: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def text(self) -> str:
        return (self.out + ("\n" if self.out and self.err else "") + self.err).strip()


class Runner:
    def run(self, argv: list[str], *, env: dict[str, str] | None = None, cwd: str | None = None,
            timeout: float | None = 120, input: str | None = None, stdin_file: str | None = None) -> Result:
        """`stdin_file` streams a file to the command's input (for large SQL imports)."""
        full_env = {**os.environ, **env} if env else None
        try:
            if stdin_file is not None:
                with open(stdin_file, "rb") as f:
                    p = subprocess.run([str(a) for a in argv], capture_output=True, env=full_env, cwd=cwd,
                                       timeout=timeout, stdin=f)
                return Result(p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"))
            p = subprocess.run([str(a) for a in argv], capture_output=True, text=True, env=full_env,
                               cwd=cwd, timeout=timeout, input=input)
        except FileNotFoundError:
            return Result(127, "", f"command not found: {argv[0]}")
        except subprocess.TimeoutExpired:
            return Result(124, "", f"timed out: {' '.join(map(str, argv))}")
        return Result(p.returncode, p.stdout, p.stderr)
