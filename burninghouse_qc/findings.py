"""Findings and the pass / review / fail verdict they produce."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


class Severity(enum.IntEnum):
    """Ordered so max() picks the worst."""

    INFO = 0
    REVIEW = 1
    FAIL = 2

    @property
    def label(self) -> str:
        return {Severity.INFO: "info", Severity.REVIEW: "review", Severity.FAIL: "fail"}[self]


class Verdict(enum.Enum):
    PASS = "pass"
    REVIEW = "review"
    FAIL = "fail"


def format_timecode(seconds: float | None) -> str:
    if seconds is None:
        return "--:--:--.--"
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{secs:05.2f}"


@dataclass
class Finding:
    detector: str           # "black" | "silence" | "text"
    kind: str               # machine-readable sub-type
    severity: Severity
    message: str
    start: float | None = None
    end: float | None = None
    confidence: float | None = None   # 0-1, detector's own certainty
    detail: dict[str, Any] = field(default_factory=dict)
    thumbnail: Path | None = None

    @property
    def duration(self) -> float | None:
        if self.start is None or self.end is None:
            return None
        return max(0.0, self.end - self.start)

    @property
    def timecode(self) -> str:
        if self.start is None:
            return "—"
        if self.end is None or self.end <= self.start:
            return format_timecode(self.start)
        return f"{format_timecode(self.start)} → {format_timecode(self.end)}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.label
        data["timecode"] = self.timecode
        data["duration"] = self.duration
        data["thumbnail"] = str(self.thumbnail) if self.thumbnail else None
        return data


def verdict_for(findings: list[Finding]) -> Verdict:
    """Worst severity wins: any FAIL fails, otherwise any REVIEW routes to review."""
    if not findings:
        return Verdict.PASS
    worst = max(f.severity for f in findings)
    if worst is Severity.FAIL:
        return Verdict.FAIL
    if worst is Severity.REVIEW:
        return Verdict.REVIEW
    return Verdict.PASS
