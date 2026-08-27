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

Status as of Session 6 — see the Progress Log for what changed.

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
- **Where renders live, and what the app may do to them** *(added Session 4)*
  → **RESOLVED: eventually a Synology, and the answer is "nothing".** Default
  routing mode is `report_only` — the app reads the render and files a report,
  never writing to, moving, renaming or deleting anything in the watched
  folder. `copy` and `move` are opt-in for a QC folder the app owns.
- **Deployment sequencing** *(settled Session 6)*
  → **Phase 1 is entirely local**: files on the edit machine's own drive,
  nothing touching the Synology, until the QC is doing what is wanted.
  **Phase 2** points `paths.input` at the NAS — a one-line change, because
  phase 1 runs in the same `report_only` mode that phase 2 deploys.
  `docs/local-trial.md` is the phase 1 runbook.
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

### Session 4 — 2026-08-26

Renders land on a **shared server**, and server integrity is critical. That
inverted the app's default behaviour: v1 moved files out of the input folder,
which is exactly wrong for storage the app does not own.

**Routing is now a mode, defaulting to the safe one.**
- `report_only` (new default) — the render is never touched. The report goes to
  the verdict folder with a symlink pointing back at the original, so staff open
  the file from the same place they read the report.
- `copy` — original stays put, a verified copy is filed.
- `move` — the old behaviour, now opt-in and documented as QC-folders-only.

**`ledger.py`** — report_only leaves nothing about the input folder changed when
a file is done, so without a record the service would re-QC the whole share on
every restart. Files are keyed by path + size + mtime, so a re-render to the
same name is correctly treated as new. Verified live: a restart queued nothing.

**`transfer.py`** — even `move` is now defensive. Same-filesystem moves are a
single atomic rename; cross-filesystem moves (any move off a share) copy to a
`.qc-partial` name, verify size and optionally checksum, atomically rename, and
only then delete the source. Any failure removes the partial and leaves the
source intact. Plus a preflight free-space check, because filling a shared
volume is its own outage.

**Rewrite detection** — the pipeline snapshots size and mtime before QC. If the
file changed by the time QC finishes, the render is left alone and the report is
flagged as describing the earlier version. The app will not move a file it no
longer understands.

**Server load** — QC reads a file ~3 times (the black/scene pass, then frame
extraction). When the input folder is on a network mount the render is now
copied to local scratch once and analysed there, so the server sees one read
and no file handle held open for minutes.

**Also:** `bhqc scan` writes reports to `qc_root/reports/` rather than beside
the source; the never-overwrite rule now covers reports as well as renders
(a real bug — a second render of the same name was clobbering the first one's
report, caught by a new test); `bhqc doctor` prints the routing mode and its
consequence in plain words.

**Tested:** 164 tests passing (up from 130). A live shared-server simulation
QC'd two files, then restarted: source md5s and mtimes unchanged, no new files
in the share, nothing re-queued on restart.

**Recommendation on local-vs-server trials:** trial locally for **phase 1**
(threshold tuning), because tuning means re-running the same files repeatedly
with `--keep-work` and clearing the ledger — churn that belongs on a local disk.
Then **phase 2** points at the real share in `report_only`, read-only, running
alongside the manual QC. Not because the server is at risk in phase 2, but
because the tool's behaviour is still changing in phase 1.

**Belt and braces, worth doing regardless:** give the account the launchd agent
runs as **read-only** access to the renders share and full rights only to the
local QC folder. That makes the guarantee a filesystem property rather than a
promise in a config file.

**What's left:**
1. **Install on the actual Mac** and run `bhqc doctor` there — still none of
   this has run on macOS.
2. **Phase 1 local pilot** on real render output, tuning per `docs/tuning.md`.
3. **Re-measure runtime on the real machine** against real footage.
4. Decide who owns the custom dictionary.
5. Set up the read-only service account on the share before phase 2.

### Session 5 — 2026-08-26

Set up the read-only service account — or rather, the half of it that can be
done from here. Creating the account needs access to the server and the Mac, so
what this session delivers is the **verification tool** and the **runbook**.

**`bhqc check-access`** (`access.py`) — proves the permissions rather than
trusting the config. It tries the operations: lists the input folder, attempts
a write there (which should fail), attempts writes in every QC folder (which
should succeed), reports whether the input is a local disk or a network share,
and cross-checks the routing mode against what the permissions actually allow.
Exits 0 when usable, 1 when something must be fixed, so it can gate a setup
script.

The write probe is a zero-byte file with an obvious name, removed immediately
in a `finally`. It is the only write this app will ever attempt against the
input folder, and its whole purpose is to confirm that the write fails.
`--no-write-probe` skips it.

It also catches a real footgun: `routing.mode = "move"` against a read-only
share fails every file. That is now a hard FAIL at check time with the fix
named, rather than a pile of confusing errors at 2am.

**`docs/readonly-account.md`** — the runbook for the part I can't do: creating
the account on Synology, QNAP, Windows Server, macOS File Sharing, Samba and
NFS; storing the password in the macOS keychain (never in the repo or config);
mounting `-o rdonly`; making the mount survive a reboot; and reading the
verification output. Includes the LaunchAgent-vs-LaunchDaemon trap — a daemon
runs outside the user session and cannot see a user-mounted share.

**Verified end to end** on a genuinely restricted account (a real non-root Unix
user against a `555` directory, since root ignores permission bits):
- `check-access` correctly reported `input folder is read-only: YES`
- a full QC run under that account produced the right verdict and report,
  source checksums and mtimes unchanged, nothing new in the share
- `mode = "move"` against that share was caught with exit 1

**Tested:** 179 tests passing (up from 164), including that the probe never
leaves a file behind.

**What's left:**
1. **Create the account on the real server** and mount it — `docs/readonly-account.md`,
   §1 and §2. Then `bhqc check-access` on the Mac is the acceptance test.
2. **Install on the actual Mac.** Still nothing has run on macOS.
3. **Phase 1 local pilot** on real render output, tuning per `docs/tuning.md`.
4. **Re-measure runtime on the real machine** against real footage.
5. Decide who owns the custom dictionary.

### Session 6 — 2026-08-27

Corrected the deployment sequencing. Sessions 4 and 5 had run ahead to the
Synology; the actual plan is **local first, NAS only once the QC is trusted**.
Nothing built for the server was wasted — it is phase 2 — but several things
assumed a share and were wrong for a local trial.

**Amendments:**
- `check-access` no longer warns that a *local* input folder is writable. It is
  a folder you own; writable is expected there. The warning now fires only for
  a writable **network share**, which is the case actually worth tightening.
  Warning about the normal case just trains people to ignore warnings.
- `bhqc init` now writes **absolute** paths. Relative paths in a config are how
  a launchd service ends up silently watching the wrong folder, since it does
  not inherit anyone's working directory.
- `bhqc init` installs the custom dictionary to `<config dir>/dictionary/` and
  points at it absolutely. **This fixed a real bug**: `custom_dictionary` was
  resolved relative to the config file, so it broke the moment `config.toml`
  lived anywhere but the repo root — which is exactly what a proper install
  does. Found by rehearsing the install rather than by reading the code. It
  also means `git pull` can no longer overwrite added brand names.
- **`bhqc forget FILE`** / **`--all`** — clears the ledger so a file is checked
  again. The tuning loop needs this: in `report_only` the file never leaves the
  input folder, so without it a re-run after a threshold change does nothing.
- `check-access`'s summary line no longer claims "read-only on the renders"
  when the input is a writable local folder.

**Decision: phase 1 trials in `report_only`, the same mode phase 2 deploys.**
Trialling with `move` would leave the ledger and the report_only path
completely unexercised, so the NAS would get a configuration nobody had run.
`mode = "move"` is documented as a safe local alternative for anyone who wants
the files physically sorted, with that trade-off stated.

**`docs/local-trial.md`** — the phase 1 runbook: install, config, a
known-answer test using generated footage with planted faults, the first real
run, the tuning loop, and only then the service install. `server-safety.md` and
`readonly-account.md` are now explicitly labelled phase 2.

**Rehearsed the whole install end to end** in a clean directory — init, doctor,
check-access, watcher, forget — which is how both bugs above surfaced.

**Tested:** 190 tests passing (up from 179), including that the configured
dictionary actually resolves and loads from a config outside the repo.

**What's left:**
1. **Run `docs/local-trial.md` on the Mac.** Steps 1-5 are ~20 minutes and end
   with a known-answer test.
2. **Phase 1 pilot** on real renders, tuning per `docs/tuning.md`.
3. **Measure runtime on real footage** — all figures so far are synthetic
   footage on a Linux container.
4. Decide who owns the custom dictionary.
5. **Phase 2** (Synology) only once phase 1 is trusted: `readonly-account.md`,
   then change `paths.input`.
