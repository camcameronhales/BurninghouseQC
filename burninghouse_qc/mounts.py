"""Detecting network-mounted watch folders.

macOS delivers filesystem events through FSEvents, which does not fire for SMB
or AFP mounts — a render dropped onto a NAS share would sit in the input folder
forever. watchdog's polling observer works anywhere, so the watcher switches to
it automatically when the input folder turns out to live on a network mount.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# A network mount's device column looks like //user@nas/renders (SMB/AFP) or
# nas:/exports/renders (NFS), rather than a local /dev/... node.
_NETWORK_FS = {"smbfs", "afpfs", "nfs", "webdav", "cifs", "ftp"}


def device_for(path: Path) -> str:
    """The mount device backing `path`, via df. Empty string if unknown."""
    try:
        proc = subprocess.run(
            ["df", "-P", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return ""
    return lines[1].split()[0]


def is_network_path(path: Path) -> bool:
    """True if `path` sits on a network share rather than a local disk."""
    device = device_for(path)
    if not device:
        return False
    if device.startswith("//") or device.startswith("\\\\"):
        return True                     # SMB / AFP
    if "://" in device:
        return True                     # WebDAV and friends
    if device.startswith("/dev/"):
        return False                    # plainly local
    # NFS looks like host:/export — a colon with no drive-letter meaning.
    head, sep, tail = device.partition(":")
    return bool(sep and tail.startswith("/") and head and not head.startswith("/"))


def should_poll(path: Path, setting: str) -> bool:
    """Resolve the `watcher.use_polling` setting for this input folder."""
    normalised = (setting or "auto").strip().lower()
    if normalised in ("true", "yes", "always", "on"):
        return True
    if normalised in ("false", "no", "never", "off"):
        return False
    return is_network_path(path)
