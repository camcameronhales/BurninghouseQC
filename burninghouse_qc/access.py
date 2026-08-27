"""Proving the QC account's permissions are what you think they are.

Setting a share to read-only is defence in depth: it makes "the app never
writes to the server" a property of the filesystem rather than a promise in a
config file. But an unverified permission setting is just another promise, so
this module actually tries the operations and reports what happened.

The write probe creates a zero-byte file with an obvious name and removes it
immediately. It is the only write this app will ever attempt against the input
folder, and its purpose is to confirm that the write *fails*.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .config import Config
from .mounts import device_for, is_network_path

PROBE_PREFIX = ".bhqc-write-probe-"


class Status(Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    advice: str | None = None


def _readable(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "does not exist"
    if not path.is_dir():
        return False, "is not a directory"
    try:
        next(iter(os.scandir(path)), None)
    except OSError as exc:
        return False, f"cannot be listed ({exc.strerror or exc})"
    return True, "readable"


def probe_write(path: Path) -> tuple[bool, str]:
    """Try to create and remove a file. Returns (write_succeeded, detail)."""
    probe = path / f"{PROBE_PREFIX}{os.getpid()}"
    try:
        with probe.open("wb"):
            pass
    except OSError as exc:
        return False, f"writes refused ({exc.strerror or exc})"
    finally:
        # Always clean up, even if the open partially succeeded.
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            return True, "WROTE A FILE AND COULD NOT REMOVE IT — check for " + probe.name
    return True, "writes are permitted"


def check_input_folder(cfg: Config, write_probe: bool = True) -> list[Check]:
    path = cfg.paths.input
    checks: list[Check] = []

    readable, detail = _readable(path)
    checks.append(
        Check(
            name="input folder is readable",
            status=Status.OK if readable else Status.FAIL,
            detail=f"{path} — {detail}",
            advice=None if readable else "QC cannot run without read access to the renders.",
        )
    )
    if not readable:
        return checks

    device = device_for(path)
    on_network = is_network_path(path)
    checks.append(
        Check(
            name="input folder location",
            status=Status.OK,
            detail=f"{device or 'unknown device'} "
                   f"({'network share' if on_network else 'local disk'})",
        )
    )

    if not write_probe:
        checks.append(
            Check(
                name="input folder is read-only",
                status=Status.SKIPPED,
                detail="skipped (--no-write-probe)",
            )
        )
        return checks

    wrote, detail = probe_write(path)
    if not wrote:
        checks.append(
            Check(
                name="input folder is read-only",
                status=Status.OK,
                detail=f"YES — {detail}",
            )
        )
    elif on_network:
        # A writable *share* is the case worth tightening: the app will not
        # write there, but a read-only account makes that a guarantee.
        checks.append(
            Check(
                name="input folder is read-only",
                status=Status.WARN,
                detail=f"NO — {detail}",
                advice=(
                    "The QC account can write to this share. Nothing in "
                    "report_only mode will write there, but making the share "
                    "read-only for this account turns that from a promise into a "
                    "guarantee. See docs/readonly-account.md."
                ),
            )
        )
    else:
        # A writable folder on the local disk is normal and expected — it is a
        # folder you own. Nothing to tighten.
        checks.append(
            Check(
                name="input folder is writable",
                status=Status.OK,
                detail=f"{detail} — expected for a local folder you own",
            )
        )
    return checks


def check_qc_folders(cfg: Config) -> list[Check]:
    """Everything the app must be able to write to."""
    targets = {
        "pass folder": cfg.paths.passed,
        "review folder": cfg.paths.review,
        "error folder": cfg.paths.error,
        "work folder": cfg.paths.work,
        "status/ledger folder": cfg.paths.status_file.parent,
        "log folder": cfg.paths.log_file.parent,
    }
    checks: list[Check] = []
    for name, path in targets.items():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            checks.append(
                Check(
                    name=name,
                    status=Status.FAIL,
                    detail=f"{path} — could not be created ({exc.strerror or exc})",
                    advice="The QC account needs full access to its own folders.",
                )
            )
            continue
        wrote, detail = probe_write(path)
        checks.append(
            Check(
                name=name,
                status=Status.OK if wrote else Status.FAIL,
                detail=f"{path} — {detail}",
                advice=None if wrote else "QC cannot file its reports without write access here.",
            )
        )
    return checks


def check_routing_consistency(cfg: Config, input_writable: bool | None) -> list[Check]:
    """Catch a config that asks for something the permissions forbid."""
    mode = (cfg.routing.mode or "report_only").strip().lower()
    if mode == "report_only":
        return [
            Check(
                name="routing mode",
                status=Status.OK,
                detail="report_only — renders are never touched",
            )
        ]
    if mode == "copy":
        return [
            Check(
                name="routing mode",
                status=Status.OK,
                detail="copy — the original stays put, a verified copy is filed",
            )
        ]
    if mode == "move":
        if input_writable is False:
            return [
                Check(
                    name="routing mode",
                    status=Status.FAIL,
                    detail="move — but the input folder is read-only, so every "
                           "file will fail to move",
                    advice="Set routing.mode = \"report_only\", or point input at a "
                           "folder this app owns.",
                )
            ]
        return [
            Check(
                name="routing mode",
                status=Status.WARN,
                detail="move — renders will be RELOCATED out of the input folder",
                advice="Only safe on a QC folder this app owns outright. Never on "
                       "shared storage.",
            )
        ]
    return [
        Check(
            name="routing mode",
            status=Status.FAIL,
            detail=f"{mode!r} is not a valid mode",
            advice='Use "report_only", "copy" or "move".',
        )
    ]


def run_all(cfg: Config, write_probe: bool = True) -> list[Check]:
    checks = check_input_folder(cfg, write_probe=write_probe)
    input_writable: bool | None = None
    for check in checks:
        if check.name == "input folder is read-only":
            if check.status is Status.OK:
                input_writable = False
            elif check.status is Status.WARN:
                input_writable = True
    checks += check_qc_folders(cfg)
    checks += check_routing_consistency(cfg, input_writable)
    return checks
