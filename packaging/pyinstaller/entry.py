"""Entry point of the packaged app (PyInstaller): the same as `python -m pipanel`."""
import multiprocessing
import sys

from pipanel.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
