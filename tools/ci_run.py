"""Run a command in CI and, when it fails, report the end of its output as a GitHub annotation (readable from the
public API without opening the job log).

    python tools/ci_run.py "<title>" -- <command> [args...]
"""
import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    title, cmd = argv[0], argv[argv.index("--") + 1:]
    shell = len(cmd) == 1   # one string: run it through the shell (pipes, globs, &&)
    p = subprocess.Popen(cmd[0] if shell else cmd, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace", env=os.environ)
    lines = []
    for line in p.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        lines.append(line.rstrip("\n"))
    code = p.wait()
    if code != 0:
        tail = "\n".join(lines[-30:]).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title={title} (exit {code})::{tail}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
