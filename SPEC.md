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

Status as of Session 3 — see the Progress Log for what changed.

- **OS of the edit machine?** (Windows vs Mac — affects service/packaging approach)
  → **RESOLVED: macOS 26.5.2 (25F84).** `docs/service-setup.md` is now a macOS
  walkthrough (launchd, `launchctl bootstrap`), and the Windows launcher has
  been dropped. Three macOS-specific behaviours were added as a result: a
  `caffeinate` assertion held only while a job runs, automatic switching to a
  polling watcher when the input folder is on an SMB/AFP share (FSEvents does
  not fire for those), and Full Disk Access guidance.
- **Pass/review/fail thresholds**: exact confidence cutoffs for what counts as "borderline" (→ review) vs "clear-cut" (→ fail) will need tuning once real OCR/detection output is seen — treat initial thresholds as a first guess to be adjusted during piloting.
  → **First guesses implemented and documented.** Every threshold lives in
  `config.toml`; `docs/tuning.md` is the pilot playbook for adjusting them.
- **Custom dictionary**: who maintains the brand/jargon word list, and how (a plain text file is simplest for v1)?
  → **Mechanism decided:** `dictionary/custom_words.txt`, one word per line,
  `#` comments, re-read on every job so edits need no restart. **Who owns it is
  still open.**
- **File formats**: what video formats/codecs need to be supported (ProRes, H.264, etc.)?
  → **RESOLVED: mp4 and mov.** `watcher.video_extensions` now defaults to
  `[".mov", ".mp4"]` so nothing else is picked up by accident. Anything FFmpeg
  decodes still works — adding a format is one line of config.
- **Report format preference**: HTML (easy, viewable in browser) vs PDF (easier to send/archive)?
  → **Resolved: HTML**, built self-contained (thumbnails embedded as base64) so
  it emails and archives like a single file, with a print stylesheet so the
  browser exports a clean PDF when one is wanted. A `.qc.json` sidecar carries
  the same data for scripting.
- **Frame sampling rate** for OCR: trade-off between thoroughness and processing time — needs tuning against typical video length.
  → **RESOLVED against the confirmed 2-10 minute clip length.** Interval
  lowered to **1.5s** with `max_frames` raised to **600**, so the full cadence
  fits a 10-minute clip without widening. 1.5s is not arbitrary: a card must be
  on screen for `sample_interval x fail_min_occurrences` (3s) to be
  fail-eligible, and shorter supers can only reach review.
  Measured **160s for a 5-minute 1080p clip** (~30s of QC per minute of video),
  after optimisation work that halved it. Still needs re-measuring on the
  actual Mac against real footage.

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

### Session 3 — 2026-08-26

Answered the three outstanding questions: **macOS 26.5.2 (25F84)**, clips
**2–10 minutes**, formats **mp4 and mov**. All three changed real decisions.

**Performance work, driven by the clip length.** A 10-minute 1080p master was
projected at ~8 minutes of QC, which was too slow to sit comfortably in an
unattended workflow. Three changes, each measured:

- `scan.py` — black detection and scene detection now share **one decode pass**
  instead of walking the file twice (`blackdetect` sits ahead of `select` in the
  filter chain, so it still sees every frame). 36s → 21s on a 5-minute clip.
- The baseline sample grid is pulled in **one FFmpeg pass** via the `fps`
  filter instead of one seek per frame. 0.42s/frame → 0.11s/frame.
- OCR input is **normalised to ~1440px tall** rather than blindly upscaled 2x.
  720p still gets 2x; 1080p now gets 1.33x instead of being blown up to 4K for
  no accuracy gain. 0.92s/frame → 0.53s/frame.

Net: a 5-minute 1080p clip went **244s → 160s while sampling more frames**
(202 vs 154). Detection was unchanged — all three planted faults still found,
and ~35 filler title cards across the clip produced zero false positives.

Sampling was then retuned for the confirmed clip length: interval 2.0s → 1.5s,
`max_frames` 400 → 600. `plan_timestamps` now widens the interval when a clip
would blow the frame budget, instead of thinning a too-long list — which keeps
the grid evenly spaced and is what makes the single-pass extraction possible.

**macOS work:**
- `power.py` — holds a `caffeinate` assertion for the duration of each job so an
  idle machine can't sleep mid-QC, and releases it while idle so the Mac still
  sleeps normally between renders.
- `mounts.py` — detects an SMB/AFP/NFS input folder and switches the watcher to
  polling. FSEvents does not fire for network mounts, so without this a render
  dropped on a NAS would sit in the input folder forever. Overridable via
  `watcher.use_polling`.
- `docs/service-setup.md` rewritten as a macOS walkthrough: modern
  `launchctl bootstrap`/`bootout`/`kickstart` (not the deprecated `load -w`),
  the LaunchAgent-vs-LaunchDaemon trade-off (a daemon can't see user-mounted
  shares), launchd's missing `PATH`, and Full Disk Access.
- `service/run-qc.bat` deleted; Windows scoped out. Nothing in the app is
  macOS-only — only the service wrapper would need rewriting.

**Also:** `video_extensions` narrowed to `.mov`/`.mp4`; the stray "How" left at
the end of the pasted brief removed.

**Tested:** 130 tests passing (up from 99), including new coverage for network
mount detection, the power assertion releasing on exception, OCR scale
normalisation, and the sampling budget under a cut-heavy clip.

**What's left:**
1. **Install on the actual Mac** and run `bhqc doctor` there — everything is
   written, none of it has run on macOS.
2. **Pilot on real render output** alongside the existing manual QC, and tune
   with `docs/tuning.md`. The thresholds have still only ever seen synthetic
   footage.
3. **Re-measure runtime on the real machine.** All figures above come from the
   Linux container this was built in, against deliberately busy synthetic
   footage. Apple Silicon and real graded material will both differ.
4. Decide who owns the custom dictionary.
5. Confirm whether renders land on a local disk or a share — it decides
   LaunchAgent vs LaunchDaemon, and whether the polling watcher kicks in.
