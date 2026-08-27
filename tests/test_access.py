"""Permission verification: proving what the QC account can and cannot do.

Read-only scenarios are simulated rather than chmod'd, because the test suite
may run as root, and root ignores the permission bits entirely.
"""

from pathlib import Path

import pytest

from burninghouse_qc import access
from burninghouse_qc.access import (
    PROBE_PREFIX,
    Status,
    check_input_folder,
    check_qc_folders,
    check_routing_consistency,
    probe_write,
    run_all,
)
from burninghouse_qc.config import Config


@pytest.fixture
def cfg(tmp_path) -> Config:
    config = Config()
    config.paths.root = tmp_path / "qc"
    config.paths.input = tmp_path / "share"
    config.paths.passed = tmp_path / "qc" / "pass"
    config.paths.review = tmp_path / "qc" / "review"
    config.paths.error = tmp_path / "qc" / "error"
    config.paths.work = tmp_path / "qc" / "work"
    config.paths.status_file = tmp_path / "qc" / "status.json"
    config.paths.log_file = tmp_path / "qc" / "qc.log"
    config.paths.input.mkdir(parents=True)
    return config


def status_of(checks, name) -> Status:
    return next(c.status for c in checks if c.name == name)


# -- the write probe itself ----------------------------------------------

def test_probe_reports_a_writable_folder(tmp_path):
    wrote, detail = probe_write(tmp_path)
    assert wrote
    assert "permitted" in detail


def test_the_probe_never_leaves_a_file_behind(tmp_path):
    """The one write this app makes to the input folder must not persist."""
    probe_write(tmp_path)
    assert list(tmp_path.glob(f"{PROBE_PREFIX}*")) == []
    assert list(tmp_path.iterdir()) == []


def test_probe_reports_a_refused_write(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", refuse)
    wrote, detail = probe_write(tmp_path)
    assert not wrote
    assert "refused" in detail


# -- the input folder ----------------------------------------------------

def test_a_read_only_share_is_reported_as_correct(cfg, monkeypatch):
    monkeypatch.setattr(access, "probe_write", lambda _p: (False, "writes refused"))
    checks = check_input_folder(cfg)
    assert status_of(checks, "input folder is readable") is Status.OK
    assert status_of(checks, "input folder is read-only") is Status.OK


def test_a_writable_share_is_flagged_but_not_fatal(cfg, monkeypatch):
    """Writable is a warning, not an error — the app still won't write there."""
    monkeypatch.setattr(access, "probe_write", lambda _p: (True, "writes are permitted"))
    checks = check_input_folder(cfg)
    check = next(c for c in checks if c.name == "input folder is read-only")
    assert check.status is Status.WARN
    assert "guarantee" in (check.advice or "")


def test_an_unreadable_share_is_fatal(cfg, monkeypatch):
    def refuse(_path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(access.os, "scandir", refuse)
    checks = check_input_folder(cfg)
    assert status_of(checks, "input folder is readable") is Status.FAIL


def test_a_missing_share_is_fatal_and_stops_there(cfg):
    """An unmounted share is the most likely real-world failure."""
    cfg.paths.input = cfg.paths.input.parent / "not-mounted"
    checks = check_input_folder(cfg)
    assert len(checks) == 1
    assert checks[0].status is Status.FAIL
    assert "does not exist" in checks[0].detail


def test_the_write_probe_can_be_skipped(cfg):
    checks = check_input_folder(cfg, write_probe=False)
    assert status_of(checks, "input folder is read-only") is Status.SKIPPED


# -- the QC folders ------------------------------------------------------

def test_qc_folders_are_created_and_verified(cfg):
    checks = check_qc_folders(cfg)
    assert all(c.status is Status.OK for c in checks), [c.detail for c in checks]
    assert cfg.paths.passed.exists() and cfg.paths.work.exists()


def test_an_unwritable_qc_folder_is_fatal(cfg, monkeypatch):
    monkeypatch.setattr(access, "probe_write", lambda _p: (False, "writes refused"))
    checks = check_qc_folders(cfg)
    assert all(c.status is Status.FAIL for c in checks)


# -- routing mode vs. what the permissions allow -------------------------

def test_report_only_is_always_fine(cfg):
    cfg.routing.mode = "report_only"
    assert check_routing_consistency(cfg, input_writable=False)[0].status is Status.OK


def test_move_against_a_read_only_share_is_caught(cfg):
    """Every file would fail to move — better to say so before it runs."""
    cfg.routing.mode = "move"
    check = check_routing_consistency(cfg, input_writable=False)[0]
    assert check.status is Status.FAIL
    assert "read-only" in check.detail


def test_move_against_a_writable_share_warns(cfg):
    cfg.routing.mode = "move"
    check = check_routing_consistency(cfg, input_writable=True)[0]
    assert check.status is Status.WARN
    assert "RELOCATED" in check.detail


def test_an_invalid_mode_is_caught_before_it_runs(cfg):
    cfg.routing.mode = "delete_everything"
    assert check_routing_consistency(cfg, input_writable=False)[0].status is Status.FAIL


# -- the whole run -------------------------------------------------------

def test_a_correctly_configured_account_passes_cleanly(cfg, monkeypatch):
    real_probe = access.probe_write

    def probe(path):
        # Read-only on the share, writable everywhere else.
        if path == cfg.paths.input:
            return False, "writes refused (Permission denied)"
        return real_probe(path)

    monkeypatch.setattr(access, "probe_write", probe)
    checks = run_all(cfg)
    assert [c for c in checks if c.status in (Status.FAIL, Status.WARN)] == []
