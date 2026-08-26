# Video QC App — Project Spec

**Purpose of this doc:** Starting context for a Claude Code session. Paste or reference this file at the start of the session so Claude has full project context without re-explaining. Update the Progress Log at the bottom at the end of each session.

---

## 1. What this is

An automated QC pipeline that watches a folder for newly rendered video files, checks them for spelling errors in on-screen graphics/text and technical issues (black frames, silence, etc.), then sorts the file into a Pass or Error folder along with a report.

## 2. Environment & constraints

- Runs on a **single, infrequently-used second edit machine**
- No multi-user UI needed — it's a background/unattended service, not an interactive app
- Should start automatically on machine boot (background service / scheduled task)
- Minimal interface: system tray icon or status file showing idle/processing/done is enough for v1. No polished GUI required.

## 3. Core workflow

1. Watch `/input` folder for new video files
2. Detect when a file has finished writing (renders can take time — need file-size-stability check or temp-extension convention, not just "file appeared")
3. Run QC pipeline on the file:
   - **Spelling/text QC**: extract frames at intervals (denser sampling during graphics-heavy sections) → OCR → spell-check against dictionary + custom word list (brand names, client names, jargon)
   - **Black frame detection**: FFmpeg `blackdetect`
   - **Silence/audio dropout detection**: FFmpeg `silencedetect`
   - *(Stretch, not v1)*: frozen frame detection, loudness spikes, resolution/aspect ratio mismatch, flash frames
4. Generate a report (HTML or PDF) — timestamped issue list, with frame thumbnails for flagged spelling issues
5. Route the file into one of three folders:
   - **Pass** → `/pass` — no issues found, or only high-confidence non-issues
   - **Review** → `/review` — low-confidence or borderline flags (e.g. an uncertain OCR guess, a single short black frame that might be intentional) that need a human eye before deciding pass/fail
   - **Fail** → `/error` — high-confidence, clear-cut issues (e.g. confirmed misspelling against dictionary, sustained black frame, audio dropout)
   - Every file gets a report regardless of which folder it lands in, so staff can see *why* it was routed where it was

## 4. Proposed tech stack

- **Python** (better tooling for video/image/OCR work than Node here, and no UI shell needed)
- **watchdog** — folder watching
- **ffmpeg-python** or direct FFmpeg calls — black frame / silence detection, frame extraction
- **Tesseract** (pytesseract) — OCR, run locally/offline first; evaluate accuracy on real footage before considering a paid cloud OCR API (Google Vision) as a fallback for stylized graphics
- **pyspellchecker** or **enchant** — spell-checking, with a custom/editable dictionary file for brand names and jargon
- **SQLite** (optional, v2) — job history / report log
- Packaged to run as a **Windows background service** (or scheduled task at boot) if the edit machine is Windows — confirm OS

## 5. Build order (recommended)

1. **Prototype the detection logic first as standalone scripts**, tested against real rendered footage:
   - Frame extraction + black frame detection
   - OCR + spellcheck pass — this is the highest-risk part for false positives, needs real-world tuning
2. Once detection accuracy is acceptable, **build the folder-watcher pipeline** that ties it together
3. **Report generation** (HTML/PDF with thumbnails and timestamps)
4. **Pass/error/review routing logic**
5. **Package as a background service** that starts on boot
6. Pilot on real render output before treating it as the sole QC gate

## 6. Open questions to resolve early in the build

Status as of Session 2 — see the Progress Log for what changed.

- **OS of the edit machine?** (Windows vs Mac — affects service/packaging approach)
  → **STILL OPEN.** Both are covered in `docs/service-setup.md` (NSSM/Task
  Scheduler for Windows, launchd for macOS) so this doesn't block anything, but
  only one of them needs doing and it can't be verified until we know which.
- **Pass/review/fail thresholds**: exact confidence cutoffs for what counts as "borderline" (→ review) vs "clear-cut" (→ fail) will need tuning once real OCR/detection output is seen — treat initial thresholds as a first guess to be adjusted during piloting.
  → **First guesses implemented and documented.** Every threshold lives in
  `config.toml`; `docs/tuning.md` is the pilot playbook for adjusting them.
- **Custom dictionary**: who maintains the brand/jargon word list, and how (a plain text file is simplest for v1)?
  → **Mechanism decided:** `dictionary/custom_words.txt`, one word per line,
  `#` comments, re-read on every job so edits need no restart. **Who owns it is
  still open.**
- **File formats**: what video formats/codecs need to be supported (ProRes, H.264, etc.)?
  → **Partly answered by the design:** anything FFmpeg can decode works. The
  watcher's `video_extensions` list is what actually gates pick-up, and
  currently covers `.mov .mp4 .mxf .m4v .avi .mkv .webm`. Still worth
  confirming what actually comes out of the render queue.
- **Report format preference**: HTML (easy, viewable in browser) vs PDF (easier to send/archive)?
  → **Resolved: HTML**, built self-contained (thumbnails embedded as base64) so
  it emails and archives like a single file, with a print stylesheet so the
  browser exports a clean PDF when one is wanted. A `.qc.json` sidecar carries
  the same data for scripting.
- **Frame sampling rate** for OCR: trade-off between thoroughness and processing time — needs tuning against typical video length.
  → **Implemented as interval + scene-change sampling** with a `max_frames`
  ceiling. Default 2s interval plus extra frames after each detected cut.
  Measured ~9s of QC for a 16s 720p clip; needs re-measuring against a real
  long-form master.

## 7. Explicitly out of scope for v1

- Multi-user access / multi-machine rollout
- Auto-update mechanism
- Polished GUI/dashboard (could be a v2 addition — simple local Flask status page)
- Cloud OCR (only if local Tesseract proves too inaccurate)

---

## Progress Log

*(Append a dated entry here at the end of each session — what was built, what was tested, what's left, any decisions made.)*

### Session 1 — [DATE]
- Spec created. No code written yet.

### Session 2 — 2026-08-26

**Built: the whole v1, steps 1–5 of the build order.**

Detection (build order step 1):
- `detectors/black.py` — FFmpeg `blackdetect`, with head/tail grace so fades
  route to review rather than failing the file.
- `detectors/silence.py` — FFmpeg `silencedetect`, including the "silent for the
  entire duration" case and files with no audio stream at all.
- `detectors/text.py` — frame sampling → Tesseract OCR → spell-check, with
  annotated thumbnails (the offending word boxed in red on its frame).
- Sampling is interval-based *plus* extra frames just after each detected scene
  change, since supers arrive on cuts. `max_frames` caps long masters and thins
  coverage evenly rather than truncating.

Pipeline and service (steps 2–5):
- `watcher.py` — watchdog feeds a queue; one worker drains it, so a burst of
  renders can't spawn concurrent FFmpeg jobs on a machine someone is editing on.
- `stability.py` — temp-extension filtering *and* a size/mtime stability poll.
- `report.py` — self-contained HTML with embedded thumbnails + JSON sidecar.
- `router.py` — pass/review/error, never overwrites, report always travels with
  the file.
- `status.py` — atomically-written `status.json` (idle/processing/done) + a log.
- `cli.py` — `scan`, `run`, `watch`, `init`, `status`, `doctor`.
- `docs/service-setup.md`, plus a Windows `.bat` and a macOS launchd plist.

**Tested:**
- 99 tests, all passing. `scripts/make_sample.py` renders synthetic clips with a
  deliberately misspelled title card ("Acheiving"), a 2s mid-programme black
  run and a 5s audio dropout; the e2e tests assert the pipeline finds exactly
  those three and that a clean clip comes back with **zero** findings.
- Live service smoke test: a chunked write to `Client_Master_v7.mp4.tmp`
  followed by a rename was correctly ignored until the rename, waited out the
  stability window, QC'd, and routed to `/error` with its report. 9.6s for a
  16s 720p clip.

**Decisions made:**
- **Direct FFmpeg calls, not `ffmpeg-python`.** The filter output is the source
  of truth and parsing it keeps the dependency surface small.
- **Report is HTML.** Self-contained, prints to PDF. (Closes an open question.)
- **British/Australian spellings are handled by rule, not by word list**
  (`variants.py`): a suspect word is transformed to its US form and re-checked,
  so `colour`/`organise`/`centre` pass while `coulour` still fails. This was
  found by piloting the first build — pyspellchecker's `en` dictionary is US
  English and flagged "COLOUR" on the very first test clip.
- **Three gates before a spelling flag can fail a file**: OCR confidence ≥85%,
  seen in ≥2 sampled frames, and word-shape filtering. A single-frame sighting
  can only ever route to review.
- **Unknown config keys raise** rather than being ignored — a typo'd threshold
  silently doing nothing is the worst failure mode for a tunable system.

**What's left:**
1. **Confirm the edit machine's OS** and actually install the service on it.
   Everything else is written; this is the one genuine blocker.
2. **Pilot on real render output** (build order step 6) — run alongside the
   existing manual QC for a couple of weeks and tune with `docs/tuning.md`.
   The thresholds have only ever seen synthetic footage.
3. **Measure QC runtime on a real long-form master** and set `max_frames` /
   `sample_interval` from that, not from a 16s test clip.
4. Confirm the delivery codecs and extend `watcher.video_extensions` if needed.
5. Decide who owns the custom dictionary.

**Stretch items still unstarted** (all one new file in `detectors/`): frozen
frame detection (`freezedetect`), loudness (`ebur128`), resolution/aspect
mismatch, flash frames. SQLite job history remains a v2 item.
