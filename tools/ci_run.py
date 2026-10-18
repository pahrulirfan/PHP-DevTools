"""Run a command in CI and, when it fails, report the end of its output as a GitHub annotation (readable from the
public API without opening the job log).

    python tools/ci_run.py "<title>" -- <command> [args...]
"""
import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):   # containers often have an ASCII locale
        stream.reconfigure(encoding="utf-8", errors="replace")
    title, cmd = argv[0], argv[argv.index("--") + 1:]
    shell = len(cmd) == 1   # one string: run it through the shell (pipes, globs, &&)
    try:
        p = subprocess.Popen(cmd[0] if shell else cmd, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors="replace", env=os.environ)
    except OSError as e:
        print(f"::error title={title} (cannot start)::{e}", flush=True)
        return 127
    lines = []
    for line in p.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        lines.append(line.rstrip("\n"))
    code = p.wait()
    if code != 0:
        fails = [l for l in lines if l.startswith(("FAIL", "Traceback", "E   "))][:25]
        picked = fails + ["..."] + lines[-15:] if fails else lines[-30:]
        tail = "\n".join(picked).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title={title} (exit {code})::{tail}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
