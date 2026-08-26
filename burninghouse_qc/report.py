"""Self-contained HTML QC report.

Thumbnails are embedded as base64 so a report is a single file that can be
copied, emailed or archived without losing its images, and printed to PDF
straight from the browser (SPEC.md §6 leaves the format open — HTML that
prints cleanly covers both).
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

from PIL import Image

from .config import ReportConfig
from .findings import Finding, Severity, Verdict
from .pipeline import QCResult

_VERDICT_COPY = {
    Verdict.PASS: ("PASS", "No issues found. Routed to the pass folder."),
    Verdict.REVIEW: (
        "NEEDS REVIEW",
        "Borderline or low-confidence flags. A human should look before this ships.",
    ),
    Verdict.FAIL: ("FAIL", "Clear-cut issues found. Routed to the error folder."),
}

_CSS = """
:root {
  --bg: #f6f7f9; --card: #ffffff; --ink: #16181d; --muted: #5f6673;
  --line: #e3e6eb; --fail: #c0392b; --review: #b7791f; --pass: #237a4b;
}
* { box-sizing: border-box; }
body { margin: 0; padding: 32px 20px 64px; background: var(--bg); color: var(--ink);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.wrap { max-width: 940px; margin: 0 auto; }
header { margin-bottom: 24px; }
h1 { font-size: 20px; margin: 0 0 4px; letter-spacing: -0.01em; }
.filename { font-size: 26px; font-weight: 650; word-break: break-all; margin: 0 0 14px; }
.sub { color: var(--muted); font-size: 13px; margin: 0; }
.verdict { display: inline-flex; align-items: center; gap: 10px; padding: 12px 18px;
  border-radius: 10px; font-weight: 700; letter-spacing: 0.06em; font-size: 14px; color: #fff; }
.verdict.pass { background: var(--pass); }
.verdict.review { background: var(--review); }
.verdict.fail { background: var(--fail); }
.verdict-note { color: var(--muted); font-size: 13px; margin: 10px 0 0; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 20px 22px; margin: 18px 0; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.09em; color: var(--muted);
  margin: 0 0 14px; font-weight: 700; }
dl.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 14px 24px; margin: 0; }
dl.meta dt { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
dl.meta dd { margin: 2px 0 0; font-variant-numeric: tabular-nums; }
.finding { border-left: 4px solid var(--line); padding: 14px 0 14px 16px; margin: 0 0 16px; }
.finding:last-child { margin-bottom: 0; }
.finding.fail { border-left-color: var(--fail); }
.finding.review { border-left-color: var(--review); }
.finding.info { border-left-color: var(--muted); }
.finding .row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px; margin-bottom: 6px; }
.tag { font-size: 10px; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase;
  padding: 3px 8px; border-radius: 5px; color: #fff; }
.tag.fail { background: var(--fail); } .tag.review { background: var(--review); }
.tag.info { background: var(--muted); }
.tc { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px;
  color: var(--muted); }
.detector { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
.msg { margin: 0 0 8px; }
.detail { font-size: 13px; color: var(--muted); margin: 0; }
.detail b { color: var(--ink); font-weight: 600; }
.thumb { margin-top: 12px; }
.thumb img { max-width: 100%; border-radius: 8px; border: 1px solid var(--line); display: block; }
.empty { color: var(--muted); }
footer { color: var(--muted); font-size: 12px; margin-top: 28px; text-align: center; }
@media print {
  body { background: #fff; padding: 0; }
  .card { break-inside: avoid; border-color: #ccc; }
  .finding { break-inside: avoid; }
}
"""


def _thumbnail_data_uri(path: Path, width: int) -> str | None:
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            if image.width > width:
                height = max(1, round(image.height * width / image.width))
                image = image.resize((width, height), Image.LANCZOS)
            import io

            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=82)
    except OSError:
        return None
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _render_finding(finding: Finding, cfg: ReportConfig, allow_thumbnail: bool) -> str:
    severity = finding.severity.label
    parts = [
        f'<div class="finding {severity}">',
        '<div class="row">',
        f'<span class="tag {severity}">{severity}</span>',
        f'<span class="tc">{html.escape(finding.timecode)}</span>',
        f'<span class="detector">{html.escape(finding.detector)}</span>',
        "</div>",
        f'<p class="msg">{html.escape(finding.message)}</p>',
    ]

    details: list[str] = []
    suggestions = finding.detail.get("suggestions")
    if suggestions:
        joined = ", ".join(html.escape(s) for s in suggestions)
        details.append(f"Did you mean <b>{joined}</b>?")
    if finding.detail.get("occurrences"):
        details.append(f"Seen in <b>{finding.detail['occurrences']}</b> sampled frame(s)")
    if finding.detail.get("ocr_confidence") is not None:
        details.append(f"OCR confidence <b>{finding.detail['ocr_confidence']}%</b>")
    if finding.detail.get("duration") is not None:
        details.append(f"Duration <b>{finding.detail['duration']:.2f}s</b>")
    if details:
        parts.append(f'<p class="detail">{" &middot; ".join(details)}</p>')

    if allow_thumbnail and finding.thumbnail and Path(finding.thumbnail).exists():
        uri = _thumbnail_data_uri(Path(finding.thumbnail), cfg.thumbnail_width)
        if uri:
            parts.append(
                f'<div class="thumb"><img alt="Frame at {html.escape(finding.timecode)}" '
                f'src="{uri}"></div>'
            )

    parts.append("</div>")
    return "".join(parts)


def render_html(result: QCResult, cfg: ReportConfig) -> str:
    verdict_key = result.verdict.value
    headline, note = _VERDICT_COPY[result.verdict]
    counts = result.counts()

    meta: list[tuple[str, str]] = [
        ("Checked", result.finished_at.astimezone().strftime("%d %b %Y, %H:%M:%S")),
        ("QC runtime", f"{result.elapsed:.1f}s"),
        ("Issues", f"{counts['fail']} fail &middot; {counts['review']} review"),
    ]
    if result.media:
        meta += [
            ("Duration", f"{result.media.duration:.2f}s"),
            ("Resolution", result.media.resolution),
            ("Frame rate", f"{result.media.fps:.3f}" if result.media.fps else "unknown"),
            ("Video codec", result.media.video_codec or "none"),
            ("Audio codec", result.media.audio_codec or "none"),
        ]
    if result.stats.get("frames_sampled") is not None:
        meta.append(("Frames OCR'd", str(result.stats.get("frames_sampled", 0))))
    if result.stats.get("scene_changes") is not None:
        meta.append(("Scene changes", str(result.stats.get("scene_changes", 0))))

    meta_html = "".join(
        f"<div><dt>{html.escape(label)}</dt><dd>{value}</dd></div>" for label, value in meta
    )

    thumbnails_used = 0
    finding_blocks: list[str] = []
    for finding in result.findings:
        allow = thumbnails_used < cfg.max_thumbnails
        block = _render_finding(finding, cfg, allow)
        if 'class="thumb"' in block:
            thumbnails_used += 1
        finding_blocks.append(block)

    findings_html = (
        "".join(finding_blocks)
        if finding_blocks
        else '<p class="empty">No issues detected by any enabled check.</p>'
    )

    error_html = ""
    if result.error:
        error_html = (
            f'<div class="card"><h2>Pipeline error</h2>'
            f'<p class="detail">{html.escape(result.error)}</p></div>'
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QC report — {html.escape(result.source.name)}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<header>
  <h1>Burninghouse QC report</h1>
  <p class="filename">{html.escape(result.source.name)}</p>
  <span class="verdict {verdict_key}">{headline}</span>
  <p class="verdict-note">{html.escape(note)}</p>
</header>
<div class="card"><h2>File</h2><dl class="meta">{meta_html}</dl></div>
{error_html}
<div class="card"><h2>Findings</h2>{findings_html}</div>
<footer>Generated by Burninghouse QC &middot; thresholds are tunable in config.toml</footer>
</div></body></html>
"""


def write_report(result: QCResult, destination_dir: Path, cfg: ReportConfig) -> Path:
    """Write `<stem>.qc.html` (and optionally `.qc.json`) into destination_dir."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    stem = result.source.stem
    html_path = destination_dir / f"{stem}.qc.html"
    html_path.write_text(render_html(result, cfg), encoding="utf-8")
    if cfg.write_json:
        json_path = destination_dir / f"{stem}.qc.json"
        json_path.write_text(
            json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8"
        )
    return html_path
