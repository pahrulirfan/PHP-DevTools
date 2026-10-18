import sys


def main() -> int:
    from .cli import is_cli
    if is_cli(sys.argv[1:]):
        from .cli import main as cli
        return cli(sys.argv[1:])
    from .app import main as run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
