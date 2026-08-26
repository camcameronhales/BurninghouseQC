"""Keeping the Mac awake while a QC job is running.

An edit machine that nobody is sitting at will happily fall asleep halfway
through a job. `caffeinate` is held only for the duration of a job rather than
for the life of the service, so the machine can still sleep normally when the
QC app is idle — which on an infrequently-used second machine is most of the
time.

On any non-macOS host this is a no-op.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
from typing import Iterator

# -i prevent idle sleep, -m prevent disk idle sleep, -s prevent system sleep
CAFFEINATE = ["/usr/bin/caffeinate", "-i", "-m", "-s"]


def supported() -> bool:
    return sys.platform == "darwin"


@contextlib.contextmanager
def keep_awake(logger=None) -> Iterator[None]:
    """Hold a power assertion for the duration of the block."""
    process: subprocess.Popen | None = None
    if supported():
        try:
            process = subprocess.Popen(
                CAFFEINATE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError as exc:
            if logger:
                logger.warning("Could not hold the machine awake: %s", exc)
            process = None
    try:
        yield
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                process.kill()
