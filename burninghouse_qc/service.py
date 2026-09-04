"""Installing the QC watcher as a macOS launchd agent.

The point of this file is that nobody should have to hand-edit a plist. Paths
are filled in from the running interpreter and the config actually in use, so
the agent cannot end up pointing at the wrong Python or the wrong folder —
which is the usual way these go wrong.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import Config

LABEL = "com.burninghouse.qc"

# launchd inherits no shell PATH, so FFmpeg and Tesseract have to be named.
# /opt/homebrew is Apple Silicon, /usr/local is Intel; listing both is harmless.
SEARCH_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(config_path: Path, python: Path, cfg: Config) -> str:
    working_dir = config_path.parent
    out_log = cfg.paths.log_file.parent / "launchd.out.log"
    err_log = cfg.paths.log_file.parent / "launchd.err.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>burninghouse_qc.cli</string>
    <string>-c</string>
    <string>{config_path}</string>
    <string>watch</string>
  </array>

  <key>WorkingDirectory</key>
  <string>{working_dir}</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{SEARCH_PATH}</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <!-- QC is background work; keep it off the cores the editor is using. -->
  <key>ProcessType</key>
  <string>Background</string>
  <key>Nice</key>
  <integer>5</integer>

  <key>StandardOutPath</key>
  <string>{out_log}</string>
  <key>StandardErrorPath</key>
  <string>{err_log}</string>
</dict>
</plist>
"""


def install(config_path: Path, cfg: Config) -> Path:
    target = agent_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_plist(config_path, Path(sys.executable), cfg), encoding="utf-8")
    return target


def is_installed() -> bool:
    return agent_path().exists()


def kickstart() -> tuple[bool, str]:
    """Restart the running service so it picks up new code.

    A launchd agent holds the code it started with. Without this, `git pull`
    updates the files on disk and changes nothing about what is actually
    running — which is a silent, easily-missed failure.
    """
    if sys.platform != "darwin" or not is_installed():
        return False, "no background service installed"
    import subprocess

    target = f"gui/{os.getuid()}/{LABEL}"
    proc = subprocess.run(
        ["launchctl", "kickstart", "-k", target],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip() or "launchctl failed"
    return True, "restarted"


def commands() -> dict[str, str]:
    uid = os.getuid()
    return {
        "start": f"launchctl bootstrap gui/{uid} {agent_path()}",
        "stop": f"launchctl bootout gui/{uid}/{LABEL}",
        "restart": f"launchctl kickstart -k gui/{uid}/{LABEL}",
        "check": f"launchctl print gui/{uid}/{LABEL}",
    }
