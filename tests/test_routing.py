"""Verdict logic and where files land."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from burninghouse_qc.config import Config
from burninghouse_qc.findings import Finding, Severity, Verdict, format_timecode, verdict_for
from burninghouse_qc.pipeline import QCResult
from burninghouse_qc.router import destination_dir, route, unique_path


def make_finding(severity: Severity) -> Finding:
    return Finding(detector="test", kind="k", severity=severity, message="m", start=1.0, end=2.0)


def test_no_findings_passes():
    assert verdict_for([]) is Verdict.PASS


def test_any_fail_routes_to_fail():
    findings = [make_finding(Severity.REVIEW), make_finding(Severity.FAIL)]
    assert verdict_for(findings) is Verdict.FAIL


def test_review_only_routes_to_review():
    assert verdict_for([make_finding(Severity.REVIEW)]) is Verdict.REVIEW


def test_info_only_still_passes():
    """Informational notes are recorded in the report but must not gate the file."""
    assert verdict_for([make_finding(Severity.INFO)]) is Verdict.PASS


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "00:00:00.00"), (9.5, "00:00:09.50"), (61.25, "00:01:01.25"), (3661.5, "01:01:01.50")],
)
def test_format_timecode(seconds, expected):
    assert format_timecode(seconds) == expected


def test_unique_path_never_overwrites(tmp_path):
    target = tmp_path / "master.mov"
    target.write_text("first")
    assert unique_path(target).name == "master (1).mov"
    (tmp_path / "master (1).mov").write_text("second")
    assert unique_path(target).name == "master (2).mov"


def build_result(source: Path, findings: list[Finding]) -> QCResult:
    now = datetime.now(timezone.utc)
    return QCResult(
        source=source,
        verdict=verdict_for(findings),
        findings=findings,
        media=None,
        started_at=now,
        finished_at=now,
        stats={},
    )


@pytest.mark.parametrize(
    "severity,folder",
    [(Severity.FAIL, "error"), (Severity.REVIEW, "review")],
)
def test_route_moves_file_and_report_together(tmp_path, severity, folder):
    cfg = Config()
    cfg.paths.root = tmp_path
    cfg.paths.passed = tmp_path / "pass"
    cfg.paths.review = tmp_path / "review"
    cfg.paths.error = tmp_path / "error"

    source = tmp_path / "input" / "spot.mov"
    source.parent.mkdir()
    source.write_text("pretend video")

    outcome = route(build_result(source, [make_finding(severity)]), cfg)

    assert outcome.destination.parent.name == folder
    assert outcome.destination.exists()
    assert outcome.report.exists()
    assert outcome.report.parent == outcome.destination.parent
    assert not source.exists(), "the source should have been moved, not copied"


def test_passing_file_still_gets_a_report(tmp_path):
    """SPEC.md §3: every file gets a report, whichever folder it lands in."""
    cfg = Config()
    cfg.paths.passed = tmp_path / "pass"
    cfg.paths.review = tmp_path / "review"
    cfg.paths.error = tmp_path / "error"

    source = tmp_path / "spot.mov"
    source.write_text("pretend video")

    outcome = route(build_result(source, []), cfg)
    assert outcome.verdict is Verdict.PASS
    assert outcome.report.exists()
    assert "No issues detected" in outcome.report.read_text()
    assert (tmp_path / "pass" / "spot.qc.json").exists()


def test_route_renames_report_to_match_a_deduplicated_video(tmp_path):
    cfg = Config()
    cfg.paths.passed = tmp_path / "pass"
    cfg.paths.passed.mkdir(parents=True)
    (cfg.paths.passed / "spot.mov").write_text("an earlier version")

    source = tmp_path / "spot.mov"
    source.write_text("pretend video")

    outcome = route(build_result(source, []), cfg)
    assert outcome.destination.name == "spot (1).mov"
    assert outcome.report.name == "spot (1).qc.html"
    assert (cfg.paths.passed / "spot (1).qc.json").exists()


def test_destination_dir_covers_every_verdict():
    cfg = Config()
    assert {destination_dir(v, cfg) for v in Verdict} == {
        cfg.paths.passed, cfg.paths.review, cfg.paths.error
    }
