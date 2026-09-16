"""Copying a file without ever risking the only copy.

The rules this module exists to enforce:

  * never leave a half-written file where a finished one is expected;
  * never start a copy that will not fit.

The copy lands under a temporary name and is renamed into place only once it
is complete, so an interrupted transfer can never be mistaken for a finished
file. `verify_hash` additionally checksums it against the source.

Routing no longer copies or moves renders, so the caller here is the pipeline,
staging a network file to local scratch before reading it several times over.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

PARTIAL_SUFFIX = ".qc-partial"
# Require this much headroom beyond the file itself before starting a copy.
SPACE_MARGIN = 1.05


class TransferError(RuntimeError):
    """Raised when a transfer could not be completed safely.

    Raising this always means the source file is still intact.
    """


@dataclass
class FileSnapshot:
    """Enough of a file's identity to notice it changed underneath us."""

    size: int
    mtime: float

    @classmethod
    def of(cls, path: Path) -> "FileSnapshot | None":
        try:
            stat = path.stat()
        except OSError:
            return None
        return cls(size=stat.st_size, mtime=stat.st_mtime)

    def matches(self, other: "FileSnapshot | None") -> bool:
        if other is None:
            return False
        # mtime resolution differs across filesystems (SMB is often 1-2s).
        return self.size == other.size and abs(self.mtime - other.mtime) < 2.0


def free_space(directory: Path) -> int:
    try:
        return shutil.disk_usage(directory).free
    except OSError:
        return 0


def check_space(source: Path, destination_dir: Path) -> None:
    """Refuse to start a copy that cannot fit. Filling a shared volume is its
    own kind of outage, so this is checked before a single byte is written."""
    try:
        needed = source.stat().st_size
    except OSError as exc:
        raise TransferError(f"Could not stat {source.name}: {exc}") from exc
    available = free_space(destination_dir)
    if available and needed * SPACE_MARGIN > available:
        raise TransferError(
            f"Not enough room in {destination_dir}: {source.name} needs "
            f"{needed / 1e9:.2f} GB, {available / 1e9:.2f} GB free."
        )


def file_digest(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.blake2b(digest_size=16)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_copy(source: Path, target: Path, verify_hash: bool = False) -> Path:
    """Copy to a temporary name, verify it, then atomically put it in place.

    Returns the target path. On any failure the partial file is removed and
    the source is left untouched.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    check_space(source, target.parent)

    partial = target.with_name(target.name + PARTIAL_SUFFIX)
    try:
        shutil.copy2(source, partial)

        source_size = source.stat().st_size
        if partial.stat().st_size != source_size:
            raise TransferError(
                f"Copy of {source.name} is {partial.stat().st_size} bytes, "
                f"expected {source_size} — transfer was truncated."
            )
        if verify_hash and file_digest(partial) != file_digest(source):
            raise TransferError(f"Copy of {source.name} does not match the original.")

        os.replace(partial, target)
    except TransferError:
        partial.unlink(missing_ok=True)
        raise
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise TransferError(f"Could not copy {source.name}: {exc}") from exc
    return target
