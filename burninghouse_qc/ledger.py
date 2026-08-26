"""A record of what has already been QC'd.

When the app leaves files where they are — the safe mode for a shared server —
nothing about the input folder changes to show a file has been done. Without a
ledger the service would re-QC the entire folder on every restart, hammering
the server for no result.

A file is identified by path, size and mtime, so a re-render to the same name
is correctly treated as a new file.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Plenty for a second edit machine, and small enough to rewrite cheaply.
MAX_ENTRIES = 5000


def file_key(path: Path) -> str | None:
    """Identity of a specific version of a file. None if it cannot be read."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A missing or corrupt ledger must not stop QC; worst case is that
            # a file gets checked twice.
            self._entries = {}
            return
        if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
            self._entries = raw["entries"]

    def seen(self, path: Path) -> bool:
        key = file_key(path)
        return key is not None and key in self._entries

    def record(self, path: Path, verdict: str, report: str | None = None) -> None:
        key = file_key(path)
        if key is None:
            return
        self._entries[key] = {
            "filename": path.name,
            "verdict": verdict,
            "report": report,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        if len(self._entries) > MAX_ENTRIES:
            # Oldest-first by recorded time; dict order is insertion order but
            # entries can be rewritten, so sort explicitly.
            ordered = sorted(self._entries.items(), key=lambda kv: kv[1].get("at", ""))
            self._entries = dict(ordered[-MAX_ENTRIES:])
        self._write()

    def forget(self, path: Path) -> None:
        key = file_key(path)
        if key:
            self._entries.pop(key, None)
            self._write()

    def _write(self) -> None:
        payload = {"entries": self._entries}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass
