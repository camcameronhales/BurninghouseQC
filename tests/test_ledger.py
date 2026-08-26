"""The processed-file record that stops report_only re-checking forever."""

import os
from pathlib import Path

from burninghouse_qc.ledger import Ledger


def make_file(path: Path, payload: bytes = b"render") -> Path:
    path.write_bytes(payload)
    return path


def test_a_recorded_file_is_not_checked_again(tmp_path):
    ledger = Ledger(tmp_path / "processed.json")
    render = make_file(tmp_path / "a.mov")

    assert not ledger.seen(render)
    ledger.record(render, "pass")
    assert ledger.seen(render)


def test_the_record_survives_a_restart(tmp_path):
    """The whole point: a service restart must not re-QC the input folder."""
    path = tmp_path / "processed.json"
    render = make_file(tmp_path / "a.mov")
    Ledger(path).record(render, "fail")

    assert Ledger(path).seen(render)


def test_a_re_render_to_the_same_name_is_treated_as_new(tmp_path):
    """Same filename, different content — it must be checked again."""
    ledger = Ledger(tmp_path / "processed.json")
    render = make_file(tmp_path / "a.mov", b"first version")
    ledger.record(render, "pass")

    make_file(render, b"a second, longer version of the render")
    assert not ledger.seen(render)


def test_a_touched_but_unchanged_file_is_treated_as_new(tmp_path):
    ledger = Ledger(tmp_path / "processed.json")
    render = make_file(tmp_path / "a.mov")
    ledger.record(render, "pass")

    stat = render.stat()
    os.utime(render, (stat.st_mtime + 120, stat.st_mtime + 120))
    assert not ledger.seen(render)


def test_a_missing_file_is_never_seen(tmp_path):
    ledger = Ledger(tmp_path / "processed.json")
    assert not ledger.seen(tmp_path / "nope.mov")


def test_forget_allows_a_recheck(tmp_path):
    ledger = Ledger(tmp_path / "processed.json")
    render = make_file(tmp_path / "a.mov")
    ledger.record(render, "pass")
    ledger.forget(render)
    assert not ledger.seen(render)


def test_a_corrupt_ledger_does_not_stop_qc(tmp_path):
    """Worst case must be a file checked twice, never a crashed service."""
    path = tmp_path / "processed.json"
    path.write_text("{not json at all")
    render = make_file(tmp_path / "a.mov")

    ledger = Ledger(path)
    assert not ledger.seen(render)
    ledger.record(render, "pass")
    assert ledger.seen(render)


def test_the_ledger_is_bounded(tmp_path, monkeypatch):
    import burninghouse_qc.ledger as ledger_module

    monkeypatch.setattr(ledger_module, "MAX_ENTRIES", 10)
    ledger = Ledger(tmp_path / "processed.json")
    for index in range(25):
        ledger.record(make_file(tmp_path / f"clip{index}.mov", bytes([index])), "pass")

    assert len(ledger._entries) <= 10
    assert ledger.seen(tmp_path / "clip24.mov"), "the newest entries must survive"
