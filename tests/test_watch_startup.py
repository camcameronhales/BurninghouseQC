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


class TestNotifications:
    """What the service actually posts as it works."""

    def _service_with(self, tmp_path, **notification_settings):
        from burninghouse_qc.config import Config
        from burninghouse_qc.watcher import QCService

        cfg = Config()
        cfg.paths.root = tmp_path
        cfg.paths.input = tmp_path / "input"
        cfg.paths.work = tmp_path / "work"
        cfg.paths.status_file = tmp_path / "status.json"
        cfg.paths.log_file = tmp_path / "qc.log"
        cfg.paths.ledger_file = tmp_path / "processed.json"
        for key, value in notification_settings.items():
            setattr(cfg.notifications, key, value)
        cfg.ensure_paths()
        svc = QCService(cfg)
        svc.logger = FakeLogger()
        return svc

    def _capture(self, svc, monkeypatch):
        sent: list[tuple[str, str]] = []
        import burninghouse_qc.watcher as watcher_module

        monkeypatch.setattr(
            watcher_module,
            "notify",
            lambda title, message, sound=None, logger=None: sent.append((title, message)),
        )
        return sent

    def _result(self, fails=0, reviews=0):
        from datetime import datetime, timezone

        from burninghouse_qc.findings import Finding, Severity, verdict_for
        from burninghouse_qc.pipeline import QCResult

        findings = [
            Finding(detector="t", kind="k", severity=Severity.FAIL, message="m")
            for _ in range(fails)
        ] + [
            Finding(detector="t", kind="k", severity=Severity.REVIEW, message="m")
            for _ in range(reviews)
        ]
        now = datetime.now(timezone.utc)
        return QCResult(
            source=Path("/x/Spot.mov"),
            verdict=verdict_for(findings),
            findings=findings,
            media=None,
            started_at=now,
            finished_at=now,
        )

    def test_a_start_banner_says_a_long_job_is_alive(self, tmp_path, monkeypatch):
        svc = self._service_with(tmp_path)
        sent = self._capture(svc, monkeypatch)
        svc._notify_start(Path("/x/Spot.mov"))
        assert sent and "Spot.mov" in sent[0][1]

    def test_the_finish_banner_carries_the_verdict_and_counts(self, tmp_path, monkeypatch):
        from burninghouse_qc.router import RouteOutcome

        svc = self._service_with(tmp_path)
        sent = self._capture(svc, monkeypatch)
        result = self._result(fails=2, reviews=1)
        outcome = RouteOutcome(
            verdict=result.verdict,
            destination=Path("/x/Spot.mov"),
            report=Path("/x/Spot.qc.html"),
            action="left_in_place",
        )
        svc._notify_finish(Path("/x/Spot.mov"), result, outcome)

        title, message = sent[0]
        assert "FAIL" in title and "Spot.mov" in title
        assert "2 fail" in message and "1 to review" in message

    def test_a_clean_file_says_no_issues(self, tmp_path, monkeypatch):
        from burninghouse_qc.router import RouteOutcome

        svc = self._service_with(tmp_path)
        sent = self._capture(svc, monkeypatch)
        result = self._result()
        outcome = RouteOutcome(
            verdict=result.verdict,
            destination=Path("/x/Spot.mov"),
            report=Path("/x/Spot.qc.html"),
            action="left_in_place",
        )
        svc._notify_finish(Path("/x/Spot.mov"), result, outcome)
        assert "PASS" in sent[0][0]
        assert "no issues" in sent[0][1]

    def test_only_when_flagged_stays_quiet_on_a_pass(self, tmp_path, monkeypatch):
        from burninghouse_qc.router import RouteOutcome

        svc = self._service_with(tmp_path, only_when_flagged=True)
        sent = self._capture(svc, monkeypatch)
        result = self._result()
        outcome = RouteOutcome(
            verdict=result.verdict,
            destination=Path("/x/Spot.mov"),
            report=Path("/x/Spot.qc.html"),
            action="left_in_place",
        )
        svc._notify_finish(Path("/x/Spot.mov"), result, outcome)
        assert sent == []

    def test_disabling_silences_both_ends(self, tmp_path, monkeypatch):
        svc = self._service_with(tmp_path, enabled=False)
        sent = self._capture(svc, monkeypatch)
        svc._notify_start(Path("/x/Spot.mov"))
        assert sent == []


class TestInterruptedJobs:
    """Restarting the service mid-file throws that file's work away. It is
    re-checked on the next start, but the scratch it left cannot clean itself.
    """

    def test_a_busy_watcher_is_detected(self, tmp_path):
        import json
        import os

        from burninghouse_qc.status import busy_with

        status = tmp_path / "status.json"
        status.write_text(
            json.dumps({"pid": os.getpid(), "state": "processing",
                        "current_file": "WestUrban.mp4"})
        )
        assert busy_with(status) == "WestUrban.mp4"

    def test_a_watcher_waiting_for_a_write_also_counts_as_busy(self, tmp_path):
        import json
        import os

        from burninghouse_qc.status import busy_with

        status = tmp_path / "status.json"
        status.write_text(
            json.dumps({"pid": os.getpid(), "state": "waiting_for_write",
                        "current_file": "WestUrban.mp4"})
        )
        assert busy_with(status) == "WestUrban.mp4"

    def test_an_idle_watcher_is_not_busy(self, tmp_path):
        import json
        import os

        from burninghouse_qc.status import busy_with

        status = tmp_path / "status.json"
        status.write_text(json.dumps({"pid": os.getpid(), "state": "idle",
                                      "current_file": None}))
        assert busy_with(status) is None

    def test_a_dead_watcher_is_not_busy(self, tmp_path):
        """A status file left saying "processing" by a crashed service must not
        block an update forever."""
        import json

        from burninghouse_qc.status import busy_with

        status = tmp_path / "status.json"
        status.write_text(json.dumps({"pid": 999999, "state": "processing",
                                      "current_file": "Stale.mp4"}))
        assert busy_with(status) is None

    def test_a_missing_status_file_is_not_busy(self, tmp_path):
        from burninghouse_qc.status import busy_with

        assert busy_with(tmp_path / "nope.json") is None
