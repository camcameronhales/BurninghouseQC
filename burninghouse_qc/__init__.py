"""Burninghouse QC — automated QC for rendered video."""

import sys

# macOS ships Python 3.9, which is what `python3` resolves to unless a newer
# one is installed and earlier on PATH. This package needs 3.11 for tomllib,
# and the failure without this check is an unhelpful ModuleNotFoundError.
if sys.version_info < (3, 11):
    raise RuntimeError(
        f"Burninghouse QC needs Python 3.11 or newer, but this is "
        f"{sys.version.split()[0]} at {sys.executable}.\n"
        f"On macOS: brew install python@3.13, then rebuild the environment with\n"
        f"  rm -rf .venv && /opt/homebrew/bin/python3.13 -m venv .venv"
    )

__version__ = "0.1.0"
