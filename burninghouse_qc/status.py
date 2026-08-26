"""Minimal 'is it working?' surface: a JSON status file plus a log.

SPEC.md §2 only asks for idle/processing/done. A status file is the least
fragile option on an unattended machine — it survives restarts, needs no UI
toolkit, and can be tailed or opened from anywhere on the network.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def setup_logging(log_file: Path, verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("burninghouse_qc")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("Could not open log file %s — logging to console only", log_file)
    return logger


class StatusFile:
    """Atomically-written JSON snapshot of what the service is doing."""

    def __init__(self, path: Path):
        self.path = path
        self._state: dict[str, Any] = {
            "state": "starting",
            "current_file": None,
            "queued": 0,
            "processed_total": 0,
            "last_result": None,
            "updated_at": None,
            "pid": os.getpid(),
        }

    def update(self, **changes: Any) -> None:
        self._state.update(changes)
        self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write()

    def record_result(self, filename: str, verdict: str, destination: str, report: str) -> None:
        self._state["processed_total"] = int(self._state.get("processed_total", 0)) + 1
        self.update(
            state="idle",
            current_file=None,
            last_result={
                "filename": filename,
                "verdict": verdict,
                "destination": destination,
                "report": report,
                "at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _write(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            # A status file that cannot be written must never stop QC running.
            pass
