"""Desktop notifications. They must never be able to affect QC."""

import subprocess

import pytest

from burninghouse_qc import notify
from burninghouse_qc.config import NotificationConfig


def test_a_plain_notification(monkeypatch):
    script = notify.build_script("Burninghouse QC", "Checking Spot.mov…")
    assert 'display notification "Checking Spot.mov…"' in script
    assert 'with title "Burninghouse QC"' in script
    assert "sound name" not in script


def test_a_sound_can_be_attached():
    assert 'sound name "Submarine"' in notify.build_script("t", "m", "Submarine")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('a "quoted" name', 'a \\"quoted\\" name'),
        ("back\\slash", "back\\\\slash"),
        ('both " and \\', 'both \\" and \\\\'),
    ],
)
def test_filenames_with_quotes_do_not_break_the_script(raw, expected):
    """Filenames routinely contain quotes; an unescaped one would make the
    AppleScript unparseable and silently drop the notification."""
    assert expected in notify.build_script("title", raw)


def test_it_is_a_no_op_off_macos(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "linux")
    assert notify.notify("t", "m") is False


def test_a_missing_osascript_is_not_fatal(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("osascript not found")

    monkeypatch.setattr(notify, "supported", lambda: True)
    monkeypatch.setattr(notify.subprocess, "run", boom)
    assert notify.notify("t", "m") is False


def test_a_hung_osascript_is_not_fatal(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=10)

    monkeypatch.setattr(notify, "supported", lambda: True)
    monkeypatch.setattr(notify.subprocess, "run", timeout)
    assert notify.notify("t", "m") is False


def test_a_refused_notification_is_reported_as_not_sent(monkeypatch):
    class FakeProc:
        returncode = 1
        stderr = "Not authorised to send Apple events"

    monkeypatch.setattr(notify, "supported", lambda: True)
    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: FakeProc())
    assert notify.notify("t", "m") is False


def test_defaults_are_on_and_cover_both_ends():
    cfg = NotificationConfig()
    assert cfg.enabled and cfg.on_start and cfg.on_finish
    assert cfg.only_when_flagged is False, "silence should not be the default"
