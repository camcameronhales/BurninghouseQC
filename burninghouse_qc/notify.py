"""Desktop notifications, so an unattended run is not invisible.

Uses osascript, which every Mac has — no extra dependency, and it works from a
LaunchAgent because that runs inside the logged-in GUI session. (It would not
work from a LaunchDaemon, which has no session to post into.)

A notification failing must never affect QC, so everything here swallows its
own errors.
"""

from __future__ import annotations

import subprocess
import sys

# AppleScript string literals take the same escapes as C, and filenames
# routinely contain quotes and backslashes.
_ESCAPES = str.maketrans({"\\": "\\\\", '"': '\\"'})


def supported() -> bool:
    return sys.platform == "darwin"


def _escape(text: str) -> str:
    return text.translate(_ESCAPES)


def build_script(title: str, message: str, sound: str | None = None) -> str:
    script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
    if sound:
        script += f' sound name "{_escape(sound)}"'
    return script


def notify(title: str, message: str, sound: str | None = None, logger=None) -> bool:
    """Post a notification. Returns whether it was sent."""
    if not supported():
        return False
    try:
        proc = subprocess.run(
            ["osascript", "-e", build_script(title, message, sound)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if logger:
            logger.debug("Notification failed: %s", exc)
        return False
    if proc.returncode != 0 and logger:
        logger.debug("Notification refused: %s", (proc.stderr or "").strip())
    return proc.returncode == 0
