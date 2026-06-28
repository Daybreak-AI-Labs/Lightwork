"""PyInstaller entry shim.

Wraps :func:`lightwork.cli.main` so PyInstaller can produce a single-file
binary. Distributed via GitHub Releases for macOS / Linux / Windows.
"""
from lightwork.cli import main

if __name__ == "__main__":
    main()
