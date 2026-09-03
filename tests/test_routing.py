"""Verdict logic and where files land."""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from burninghouse_qc.config import Config
from burninghouse_qc.findings import Finding, Severity, Verdict, format_timecode, verdict_for
from burninghouse_qc.pipeline import QCResult
from burninghouse_qc.router import destination_dir, route, unique_path
from burninghouse_qc.transfer import FileSnapshot, TransferError


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


def make_config(tmp_path, mode="report_only") -> Config:
    cfg = Config()
    cfg.paths.root = tmp_path
    cfg.paths.passed = tmp_path / "pass"
    cfg.paths.review = tmp_path / "review"
    cfg.paths.error = tmp_path / "error"
    cfg.paths.work = tmp_path / "work"
    cfg.routing.mode = mode
    cfg.routing.symlink_in_verdict_folder = False
    cfg.report.write_json = True   # most routing tests assert on the sidecar
    return cfg


# -- the default: never touch the render ---------------------------------

@pytest.mark.parametrize(
    "severity,folder",
    [(Severity.FAIL, "error"), (Severity.REVIEW, "review")],
)
def test_report_only_leaves_the_render_exactly_where_it_was(tmp_path, severity, folder):
    """The default mode, and the only one safe to point at a shared server."""
    cfg = make_config(tmp_path)
    source = tmp_path / "server" / "spot.mov"
    source.parent.mkdir()
    source.write_text("pretend video")

    outcome = route(build_result(source, [make_finding(severity)]), cfg)

    assert outcome.action == "left_in_place"
    assert outcome.source_untouched
    assert source.exists(), "the source must not be touched in report_only mode"
    assert source.read_text() == "pretend video"
    assert outcome.destination == source
    assert outcome.report.parent.name == folder
    assert outcome.report.exists()


def test_report_only_writes_nothing_into_the_source_folder(tmp_path):
    """Nothing at all should appear next to the render on the server."""
    cfg = make_config(tmp_path)
    source = tmp_path / "server" / "spot.mov"
    source.parent.mkdir()
    source.write_text("pretend video")
    before = set(source.parent.iterdir())

    route(build_result(source, [make_finding(Severity.FAIL)]), cfg)

    assert set(source.parent.iterdir()) == before


def test_report_only_can_drop_a_shortcut_to_the_original(tmp_path):
    cfg = make_config(tmp_path)
    cfg.routing.symlink_in_verdict_folder = True
    source = tmp_path / "server" / "spot.mov"
    source.parent.mkdir()
    source.write_text("pretend video")

    outcome = route(build_result(source, [make_finding(Severity.FAIL)]), cfg)

    link = outcome.report.parent / "spot.mov"
    assert link.is_symlink()
    assert link.resolve() == source.resolve()
    assert source.exists()


# -- copy and move -------------------------------------------------------

def test_copy_mode_leaves_the_original_and_verifies_the_copy(tmp_path):
    cfg = make_config(tmp_path, mode="copy")
    source = tmp_path / "server" / "spot.mov"
    source.parent.mkdir()
    source.write_bytes(b"pretend video payload")

    outcome = route(build_result(source, []), cfg)

    assert outcome.action == "copied"
    assert source.exists(), "copy mode must not remove the original"
    assert outcome.destination.read_bytes() == source.read_bytes()


@pytest.mark.parametrize(
    "severity,folder",
    [(Severity.FAIL, "error"), (Severity.REVIEW, "review")],
)
def test_move_mode_relocates_the_render(tmp_path, severity, folder):
    cfg = make_config(tmp_path, mode="move")
    source = tmp_path / "input" / "spot.mov"
    source.parent.mkdir()
    source.write_text("pretend video")

    outcome = route(build_result(source, [make_finding(severity)]), cfg)

    assert outcome.action == "moved"
    assert outcome.destination.parent.name == folder
    assert outcome.destination.exists()
    assert outcome.report.parent == outcome.destination.parent
    assert not source.exists()


def test_move_can_be_overridden_to_report_only_from_the_cli(tmp_path):
    """`bhqc run --no-move` must be able to override a configured move."""
    cfg = make_config(tmp_path, mode="move")
    source = tmp_path / "spot.mov"
    source.write_text("pretend video")

    outcome = route(build_result(source, []), cfg, move=False)
    assert outcome.action == "left_in_place"
    assert source.exists()


def test_an_unknown_routing_mode_is_rejected(tmp_path):
    cfg = make_config(tmp_path, mode="delete")
    source = tmp_path / "spot.mov"
    source.write_text("pretend video")
    with pytest.raises(ValueError, match="routing.mode"):
        route(build_result(source, []), cfg)


# -- the report is written whatever happens ------------------------------

def test_passing_file_still_gets_a_report(tmp_path):
    """SPEC.md §3: every file gets a report, whichever folder it lands in."""
    cfg = make_config(tmp_path, mode="move")
    source = tmp_path / "spot.mov"
    source.write_text("pretend video")

    outcome = route(build_result(source, []), cfg)
    assert outcome.verdict is Verdict.PASS
    assert outcome.report.exists()
    assert "No issues detected" in outcome.report.read_text()
    assert (tmp_path / "pass" / "spot.qc.json").exists()


def test_route_renames_report_to_match_a_deduplicated_video(tmp_path):
    cfg = make_config(tmp_path, mode="move")
    cfg.paths.passed.mkdir(parents=True)
    (cfg.paths.passed / "spot.mov").write_text("an earlier version")

    source = tmp_path / "spot.mov"
    source.write_text("pretend video")

    outcome = route(build_result(source, []), cfg)
    assert outcome.destination.name == "spot (1).mov"
    assert outcome.report.name == "spot (1).qc.html"
    assert (cfg.paths.passed / "spot (1).qc.json").exists()


def test_report_only_does_not_clobber_an_earlier_report(tmp_path):
    """Two renders of the same name over time must each keep their report."""
    cfg = make_config(tmp_path)
    source = tmp_path / "server" / "spot.mov"
    source.parent.mkdir()
    source.write_text("pretend video")

    first = route(build_result(source, []), cfg)
    second = route(build_result(source, []), cfg)

    assert first.report != second.report
    assert first.report.exists() and second.report.exists()


# -- things going wrong --------------------------------------------------

def test_a_render_rewritten_mid_qc_is_left_alone(tmp_path):
    """If the file changed while we checked it, the report is stale — say so
    and do not move a file we no longer understand."""
    cfg = make_config(tmp_path, mode="move")
    source = tmp_path / "spot.mov"
    source.write_text("original render")
    snapshot = FileSnapshot.of(source)

    source.write_text("somebody re-rendered over the top of it")
    os.utime(source, (time.time() + 30, time.time() + 30))

    outcome = route(build_result(source, []), cfg, source_snapshot=snapshot)

    assert outcome.action == "left_in_place"
    assert source.exists()
    assert "changed while it was being checked" in (outcome.warning or "")


def test_an_unchanged_render_passes_the_snapshot_check(tmp_path):
    cfg = make_config(tmp_path, mode="move")
    source = tmp_path / "spot.mov"
    source.write_text("original render")

    outcome = route(build_result(source, []), cfg, source_snapshot=FileSnapshot.of(source))
    assert outcome.action == "moved"


def test_a_failed_transfer_leaves_the_source_intact(tmp_path, monkeypatch):
    cfg = make_config(tmp_path, mode="move")
    source = tmp_path / "spot.mov"
    source.write_text("pretend video")

    def boom(*args, **kwargs):
        raise TransferError("destination volume went away")

    monkeypatch.setattr("burninghouse_qc.router.safe_move", boom)
    outcome = route(build_result(source, []), cfg)

    assert outcome.action == "left_in_place"
    assert source.exists(), "a failed move must never lose the render"
    assert "volume went away" in (outcome.warning or "")
    assert outcome.report.exists(), "the report is still written"


def test_a_vanished_source_is_reported_not_crashed(tmp_path):
    cfg = make_config(tmp_path, mode="move")
    source = tmp_path / "gone.mov"
    outcome = route(build_result(source, []), cfg)
    assert outcome.action == "left_in_place"
    assert "disappeared" in (outcome.warning or "")


def test_destination_dir_covers_every_verdict():
    cfg = Config()
    assert {destination_dir(v, cfg) for v in Verdict} == {
        cfg.paths.passed, cfg.paths.review, cfg.paths.error
    }
