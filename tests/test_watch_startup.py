"""What the watcher says at start-up.

An idle watcher looks identical whether the input folder is empty, full of
files in a format it ignores, or full of files it has already checked.
Distinguishing those is not the operator's job.
"""

from pathlib import Path

import pytest

from burninghouse_qc.config import Config
from burninghouse_qc.ledger import Ledger
from burninghouse_qc.watcher import QCService


class FakeLogger:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def _record(self, level, msg, *args):
        self.messages.append((level, msg % args if args else msg))

    def info(self, msg, *args, **kw):
        self._record("info", msg, *args)

    def warning(self, msg, *args, **kw):
        self._record("warning", msg, *args)

    def debug(self, msg, *args, **kw):
        self._record("debug", msg, *args)

    def exception(self, msg, *args, **kw):
        self._record("exception", msg, *args)

    @property
    def text(self) -> str:
        return "\n".join(m for _, m in self.messages)


@pytest.fixture
def service(tmp_path) -> QCService:
    cfg = Config()
    cfg.paths.root = tmp_path
    cfg.paths.input = tmp_path / "input"
    cfg.paths.passed = tmp_path / "pass"
    cfg.paths.review = tmp_path / "review"
    cfg.paths.error = tmp_path / "error"
    cfg.paths.work = tmp_path / "work"
    cfg.paths.status_file = tmp_path / "status.json"
    cfg.paths.log_file = tmp_path / "qc.log"
    cfg.paths.ledger_file = tmp_path / "processed.json"
    cfg.paths.ensure()

    svc = QCService(cfg)
    svc.logger = FakeLogger()
    return svc


def test_an_empty_folder_says_so(service):
    service.enqueue_existing()
    assert "Input folder is empty" in service.logger.text
    assert ".mov" in service.logger.text, "it should name the formats it wants"
    assert service.queue.qsize() == 0


def test_unsupported_formats_are_called_out(service):
    """The silent-failure case: files are there, nothing happens, no reason given."""
    for name in ("master.mxf", "master.avi", "notes.txt"):
        (service.cfg.paths.input / name).write_bytes(b"x")

    service.enqueue_existing()

    levels = [level for level, _ in service.logger.messages]
    assert "warning" in levels, "this deserves a warning, not an info line"
    text = service.logger.text
    assert ".mxf" in text and ".avi" in text
    assert "video_extensions" in text, "it should say how to fix it"
    assert service.queue.qsize() == 0


def test_valid_files_are_queued_and_counted(service):
    for name in ("a.mov", "b.mp4"):
        (service.cfg.paths.input / name).write_bytes(b"x")

    service.enqueue_existing()

    assert service.queue.qsize() == 2
    assert "Queued 2 file(s)" in service.logger.text


def test_a_mix_reports_both(service):
    (service.cfg.paths.input / "good.mov").write_bytes(b"x")
    (service.cfg.paths.input / "bad.mxf").write_bytes(b"x")

    service.enqueue_existing()

    assert service.queue.qsize() == 1
    text = service.logger.text
    assert "Queued 1 file(s)" in text
    assert "Ignoring 1 file(s)" in text


def test_already_checked_files_are_explained_not_silently_skipped(service):
    render = service.cfg.paths.input / "done.mov"
    render.write_bytes(b"x")
    Ledger(service.cfg.paths.ledger_file).record(render, "pass")
    service.ledger = Ledger(service.cfg.paths.ledger_file)

    service.enqueue_existing()

    assert service.queue.qsize() == 0
    text = service.logger.text
    assert "already checked" in text.lower()
    assert "forget" in text, "it should name the command that re-checks"


def test_hidden_files_are_not_counted_as_wrong_format(service):
    """.DS_Store must not produce a warning about unsupported formats."""
    (service.cfg.paths.input / ".DS_Store").write_bytes(b"x")
    (service.cfg.paths.input / "._master.mov").write_bytes(b"x")

    service.enqueue_existing()

    assert "Input folder is empty" in service.logger.text
    assert "warning" not in [level for level, _ in service.logger.messages]


class TestConcurrentWatchers:
    """Two watchers on one folder double-process everything."""

    def test_a_live_watcher_is_detected(self, tmp_path):
        import json
        import os

        from burninghouse_qc.status import running_pid

        status = tmp_path / "status.json"
        status.write_text(json.dumps({"pid": os.getppid(), "state": "idle"}))
        assert running_pid(status) == os.getppid()

    def test_our_own_pid_is_not_reported(self, tmp_path):
        import json
        import os

        from burninghouse_qc.status import running_pid

        status = tmp_path / "status.json"
        status.write_text(json.dumps({"pid": os.getpid(), "state": "idle"}))
        assert running_pid(status) is None

    def test_a_dead_pid_is_not_reported(self, tmp_path):
        import json

        from burninghouse_qc.status import running_pid

        status = tmp_path / "status.json"
        status.write_text(json.dumps({"pid": 999999, "state": "idle"}))
        assert running_pid(status) is None

    def test_a_cleanly_stopped_watcher_is_not_reported(self, tmp_path):
        import json
        import os

        from burninghouse_qc.status import running_pid

        status = tmp_path / "status.json"
        status.write_text(json.dumps({"pid": os.getppid(), "state": "stopped"}))
        assert running_pid(status) is None

    def test_a_missing_status_file_is_fine(self, tmp_path):
        from burninghouse_qc.status import running_pid

        assert running_pid(tmp_path / "nope.json") is None
