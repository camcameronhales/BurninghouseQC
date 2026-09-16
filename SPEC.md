# Video QC App — Project Spec

**Purpose of this doc:** Starting context for a Claude Code session. Paste or reference this file at the start of the session so Claude has full project context without re-explaining. Update the Progress Log at the bottom at the end of each session.

---

## 1. What this is

An automated QC pipeline that watches a folder for newly rendered video files, checks them for spelling errors in on-screen graphics/text and technical issues (black frames, silence, etc.), then sorts the file into a Pass or Error folder along with a report.

## 2. Environment & constraints

- Runs on a **single, infrequently-used second edit machine**
  *(Revised Session 11: two machines, each an independent install against its
  own local storage. Whichever machine did the render does its own QC. Nothing
  shared between them, and no NAS access.)*
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
     *(Session 20: a flashed-graphic detector was brought forward after a real
     client deliverable turned out to contain exactly this error and passed
     clean.)*
4. Generate a report (HTML or PDF) — timestamped issue list, with frame thumbnails for flagged spelling issues
5. Route the file into one of three folders:
   - **Pass** → `/pass` — no issues found, or only high-confidence non-issues
   - **Review** → `/review` — low-confidence or borderline flags (e.g. an uncertain OCR guess, a single short black frame that might be intentional) that need a human eye before deciding pass/fail
   - **Fail** → `/error` — high-confidence, clear-cut issues (e.g. confirmed misspelling against dictionary, sustained black frame, audio dropout)
   - Every file gets a report regardless of which folder it lands in, so staff can see *why* it was routed where it was
   - *(Revised in Session 9 after seeing it in use: the three-folder structure
     was overkill. Reports are read whatever the verdict, so the report is now
     written next to the render and nothing is sorted. The pass/review/fail
     distinction still drives the verdict shown in the report.)*

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

### Session 7 — 2026-08-27

Phase 1 install started on the real Mac. Homebrew, **FFmpeg 9.0.1** and
**Tesseract 5.5.3** are in — step 1 of `docs/local-trial.md` complete.

FFmpeg 9 is three majors newer than the 6.x this was built against, so I
audited the flags before the known-answer test could hit a wall. One real
problem: **`-vsync 0` has been deprecated since 5.1** (it warns on 6.x) and may
be removed in 9.x. Replaced with a version-aware shim in `ffmpeg_tools`:
`-fps_mode passthrough` on 5.1+, `-vsync 0` below that, chosen from the
detected version. Every other flag used (`-ss`, `-vf`, `-af`, `-frames:v`,
`-f null`, `-an`, `-vn`, `-loglevel`) is stable.

`bhqc doctor` now prints the FFmpeg version — the first thing worth knowing
when a filter behaves unexpectedly — and warns below 4.3.

A new e2e test asserts no flag we pass draws a deprecation notice. Writing it
produced a nice own-goal: pytest names its temp directory after the test, so
`test_no_deprecated_flags_reach_ffmpeg` appeared in ffmpeg's echoed output path
and matched the test's own search string. Now filtered.

**Tested:** 202 tests passing (up from 190).

### Session 8 — 2026-08-27

Phase 1 install completed on the Mac through step 5, and the known-answer test
did its job — it caught a false positive that would otherwise have been found
on real work.

**Install issues found on the real machine** (all fixed, none in the QC logic):
1. `python3` on macOS is Apple's 3.9, so the venv was unusable — 3.9 lacks
   `tomllib` and its pip cannot do editable installs. The package now raises a
   clear version error instead of failing on something unrelated, and the
   runbook installs `python@3.13` explicitly.
2. `-vsync 0` is deprecated since FFmpeg 5.1 and the Mac runs 9.0.1. Replaced
   with a version-aware `-fps_mode passthrough` shim.
3. The sample generator only looked for Arial in `/Library/Fonts`, which modern
   macOS does not use.
4. **Homebrew's FFmpeg ships without `drawtext`** (it needs libfreetype and,
   since FFmpeg 7, libharfbuzz). Title cards are now rendered with Pillow — a
   dependency already — and composited with `overlay`, removing the dependency
   entirely. `doctor` now also checks that the five filters the pipeline
   actually needs are present, so a missing filter surfaces during setup.

**The real find: a false positive on the clean clip.** Tesseract 5.5.3 read
"COLOUR" as **"gOLOUR"** at 74% confidence and it was flagged for review — on
the clip that is supposed to come back with nothing. Different Tesseract
version and different font to the Linux box, so it had never appeared here.

The fix is a **case-shape filter** (`spelling.require_normal_case`, on by
default): only tokens capitalised like real words are checked — lowercase,
Title Case, or ALL CAPS. `gOLOUR` is none of those. The reasoning is that a
person typing a word wrong does not change its case halfway through, so an odd
case shape is the signature of a misread character rather than a misspelling.
It also catches `PROFESSlONAL` (capital I read as lowercase l), a very common
OCR failure.

Deliberate trade-off: mixed-case brand names (ProRes, iPhone) are no longer
checked either. That costs nothing — they are spelled correctly — and they
belong in the custom dictionary regardless.

Verified: the faulty clip still yields exactly the three planted findings and
the clean clip yields none, while `Acheiving`, `recieve`, `SEPERATE` and
friends are all still flagged.

**Tested:** 223 tests passing (up from 202).

**What's left:**
1. Re-run step 5 on the Mac to confirm the clean clip comes back clean there.
2. Step 6 — first run against real renders.
3. Tune, and measure runtime on real footage.
4. Decide who owns the custom dictionary.
5. Phase 2 (Synology) once phase 1 is trusted.

### Session 9 — 2026-09-03

First real renders through the system on the Mac. Two files, both FAIL, and on
review the findings were **false positives** — noted for tuning, details still
to come. Worth recording the reaction: "better than missing everything", which
is the right instinct and matches how the thresholds are set.

**Output structure reworked on feedback.** The three verdict folders were
overkill: reports get read whatever the verdict, so filing them by outcome just
scatters them away from the thing they describe.

New default `routing.mode = "alongside"`:
- the report is written next to the render as `<name>.qc.html`
- no pass/review/error folders — they are no longer even created
- no `.qc.json` sidecar (`report.write_json` now defaults off)
- no symlink/alias (`symlink_in_verdict_folder` now defaults off)
- the render is still never moved, renamed or altered

`report_only`, `copy` and `move` remain for the cases that want them.
`report.verdict_in_filename` is a new option giving `Spot [FAIL].qc.html` for
triage without opening anything; off by default.

**This changes the phase 2 story and needs a decision.** `alongside` writes a
small HTML file into the watched folder — on the Synology that is a write to
the server. It is still true in every mode that renders are never moved,
renamed or altered, but "the app writes nothing to the share" now only holds
under `report_only`. A filesystem-enforced read-only share
(`readonly-account.md`) requires `report_only`. `bhqc check-access` now fails
loudly when the mode and the permissions disagree, in both directions.

Verified end to end: two files through the watcher leave the input folder
holding exactly the two renders and their two reports, nothing else, and
`qc_root` holds no verdict folders at all.

**Tested:** 237 tests passing (up from 229).

**What's left:**
1. Get the false-positive details and tune (custom dictionary first — most
   false positives on real work are proper nouns).
2. Measure runtime on a real master.
3. Continue the phase 1 pilot alongside manual QC.
4. Decide `alongside` vs `report_only` for the Synology before phase 2.

### Session 10 — 2026-09-03

Five real client deliverables through the system (MMR executive interviews,
1080p, 1m39s–2m15s). The reports were well received; two systematic false
positives showed up that would have fired on every file of this type.

**1. Surnames failed every interview.** "Rothberg" (96% confidence, 4 frames)
and "Gullery" (91%, 5 frames) were both flagged as misspellings — they are the
talent's names in the lower third. A spell-checker fundamentally cannot
validate a surname, and no dictionary will ever hold every name a client sends.
Left alone this makes FAIL meaningless for interview work, which is most of it.

Fix: `spelling.skip_proper_nouns` (on by default). A Title-case word sitting
beside another Title-case word is taken as a name and skipped. Stopwords are
excluded from establishing that context, so "Acheiving The Perfect Shot" still
catches the typo. A name standing alone with no forename beside it is still
checked, and goes in the custom dictionary.

**2. Head/tail silence flagged on all five.** 1.41s–2.69s of silence at the
tail — normal handles. On the Neil Walsh file it was the *only* finding, which
made an otherwise clean deliverable come back NEEDS REVIEW.

Fix: `black.edge_severity` and `silence.edge_severity`, both defaulting to
`info`. Fades and handles are recorded in the report but no longer affect the
verdict. `review` and `ignore` are available.

**Real runtime, at last.** 26.2s / 33.8s / 38.5s for 99s / 127s / 135s of
1080p — a consistent **~16 seconds of QC per minute of video**. A 10-minute
master is about 2m 40s. That is roughly twice as fast as the synthetic
benchmarks on the Linux container, as expected.

**`bhqc install-service`** — writes the launchd agent with the venv's Python,
the real config path and the real folders filled in, then prints the
`launchctl bootstrap` line. No hand-edited plist, which is the usual way these
end up pointing at the system Python or an empty folder. `bhqc
uninstall-service` removes it.

Regression checked: the planted-fault clip still yields exactly its three
findings and the clean clip still yields none.

**Tested:** 267 tests passing (up from 237).

**What's left:**
1. Install the background service on the Mac and confirm it survives a reboot.
2. Keep running deliverables through it alongside manual QC.
3. Add client and talent names to the custom dictionary as they come up.
4. Decide `alongside` vs `report_only` for the Synology before phase 2.

### Session 11 — 2026-09-03

**Deployment model settled: two independent installs, local storage only.**
Whichever machine performs the render does the QC on that machine, against its
own drive. Neither install touches the Synology, and nothing is shared between
them.

That retires the shared-storage work rather than wasting it — `mounts.py`
(FSEvents vs polling), `work_from_local_copy`, `server-safety.md` and
`readonly-account.md` all remain, tested, but are dormant: the network-mount
detection simply finds a local disk and does nothing. The docs are now marked
as not in use rather than as an upcoming phase.

The one thing that does **not** stay in sync by itself is
`dictionary/custom_words.txt`. Each machine has its own copy, and a client name
added on one will not be known to the other. For two machines a manual copy is
the pragmatic answer; worth revisiting if it becomes a nuisance.

Also fixed this session: `init` created the folder named by `--input` even when
it refused to overwrite an existing config, leaving a folder nothing watched;
and the watcher now warns when a second watcher is already running against the
same config, which becomes easy to do once the launchd service is installed.

**Tested:** 277 tests passing.

### Session 12 — 2026-09-04

Re-ran the five MMR interview deliverables with the Session 10 fixes.

**Before:** 4 FAIL, 1 REVIEW, 0 PASS.
**After:** 3 PASS, 2 REVIEW, 0 FAIL.

Rothberg, Gullery and Walsh are clean — the surname failures are gone and the
tail handles now show as `info` without affecting the verdict.

Branson and Menzel still had two review flags each, and all four were the same
new pattern: **fragments of words caught mid-animation**. `nson` (the tail of
"Branson"), `offic` (the head of "office"), `llent`, and `Nenzel` ("Menzel"
with the M misread). All at 21–22.5s, i.e. while the lower third wipes on, and
every one reported as "only in one sampled frame".

`Nenzel` is worth noting: it slipped past the proper-noun rule because
mid-animation "Kate" was not yet visible, so there was no Title-case neighbour
to establish it as a name.

Fix: **`text.report_min_occurrences` (default 2)** — a word must appear in at
least two sampled frames to be reported at all. An animation fragment exists
for a single frame; a real super holds for seconds and is sampled repeatedly.
Set to 1 to see everything.

The trade-off, stated plainly: a misspelling on a card shown for less than
~3 seconds may now be missed. Given the alternative was a flag on essentially
every animated lower third, and that a single-frame flag could only ever reach
"review" anyway, this is the right side to err on — but it is the first change
that genuinely reduces sensitivity rather than just cutting noise.

**Also fixed:** the report said "Routed to the pass folder" / "Routed to the
error folder" — folders that do not exist in the default routing mode. The
verdict note now states the verdict without describing filing that does not
happen.

**Tested:** 284 tests passing (up from 277), including the four real fragments
from this client work as regression cases.

### Session 13 — 2026-09-04

Added **`bhqc update`**: pulls the latest code and restarts the launchd service
in one step.

The failure mode it removes is silent and easy to hit. A launchd agent holds
the code it started with, so after `git pull` the files on disk are new and the
running service is not — with no error and nothing to notice. Rather than rely
on remembering `launchctl kickstart` after every pull, the two steps are now
one command, and it reports honestly when it cannot restart (leaving you on the
old code) rather than claiming success.

`install-service` now ends by printing the update rule on screen, so it is seen
at the moment the service is installed rather than only living in a doc. The
running protocol in `local-trial.md` leads with it.

**Tested:** 287 tests passing (up from 284), including that the install output
actually contains the instruction.

### Session 14 — 2026-09-11

**Clean baseline reached.** All five MMR executive interview deliverables now
come back **PASS**, with nothing but an info-level note about tail handles.

    Session 10:  4 FAIL, 1 REVIEW, 0 PASS
    Session 12:  0 FAIL, 2 REVIEW, 3 PASS
    Session 14:  0 FAIL, 0 REVIEW, 5 PASS

Runtime across the five, on real 1080p: 15.3–17.6s of QC per minute of video,
averaging **16.3s per minute**. Consistent enough to plan around — a 10-minute
master is about 2m 45s.

Also simplified the second-machine dictionary instructions, which had reached
for `scp` when AirDrop or copy-and-paste is the honest answer for one small
text file. Noted that a fresh install ships 20 entries, so if `doctor` reports
20 on both machines there is nothing to copy at all.

**The outstanding gap:** three rounds of noise reduction have taken this from
crying wolf on every file to silence on every file, but **no run so far has
contained a real error**. The synthetic clip proves the detectors work; a real
deliverable with a known mistake in it is the test that has not happened, and
until it does the false-negative rate is unmeasured.

### Session 15 — 2026-09-16

Second install is up on the main suite and picking up files correctly.

**Added desktop notifications.** An unattended background service is invisible
by design, which meant the only way to know it was working was to go and read
the log — the question that prompted this was literally "is anything
happening?". Now a banner when a file starts, and another with the verdict when
it finishes.

Uses `osascript`, which every Mac has, so no new dependency. It works from the
LaunchAgent because that runs inside the logged-in GUI session (it would not
from a LaunchDaemon, which has no session to post into). Every failure path is
swallowed — a notification must never be able to affect QC.

`only_when_flagged` is off by default so the first days are visibly alive; it
is the setting to turn on once the novelty wears off, at which point silence
means everything passed.

One macOS quirk documented rather than worked around: the first banner posts
under "Script Editor" in System Settings > Notifications, because that is what
macOS attributes osascript to. That is where to allow them through a Focus mode
or silence them.

**Tested:** 302 tests passing (up from 287), including that filenames
containing quotes or backslashes cannot break the AppleScript, and that a
missing or hung `osascript` is not fatal.

### Session 16 — 2026-09-16

`bhqc update` was run while a file was mid-QC, and the report never appeared.
Cause: `update` ends in `launchctl kickstart -k`, which kills the running
service — including whatever job it was part-way through. A fix for one silent
failure introduced another.

Two fixes:

**`update` no longer interrupts a job silently.** It reads the status file
first and refuses if a live watcher is mid-file, naming the file and explaining
that the work would be thrown away. `--force` interrupts anyway. A stale status
file left by a crashed service cannot block an update forever, because the
check requires the recorded pid to still be alive.

**Scratch left by interrupted jobs is now cleared at start-up.** A job cleans
up after itself, but one killed part-way cannot — and those directories hold
extracted frames and sometimes a staged copy of the render. Left alone they
quietly consume gigabytes. A live job holds its directory for minutes, so
anything over six hours old can only be an orphan.

Worth recording that the pipeline recovered on its own: the ledger is only
written after routing completes, so the interrupted file was never marked done
and `enqueue_existing` picked it up again on restart. The design held; the
tooling around it did not.

**Tested:** 311 tests passing (up from 302).

### Session 17 — 2026-09-16

Reading a real log turned up two things beyond the file being asked about.

**The interruption recovery worked as designed.** 15:48:57 restart, file
re-queued from the input folder, QC restarted at 15:49:13 — exactly the path
Session 16 predicted, confirmed in production rather than argued from the code.

**Two watchers had been running at once**, on 14 Sep and again on 16 Sep. The
Session 11 guard caught both and logged a warning, and it only fires when the
other process is genuinely alive, so these were real. Most likely a manual
`bhqc watch` left running alongside the background service. Worth noting the
guard warns but does not prevent — deliberate, since refusing to start would
be worse if the detection were ever wrong, but it does mean the warning has to
be acted on.

**Fixed: our own reports were being counted as unsupported input.** Every
start-up logged "Ignoring 7 file(s) that are not .mov or .mp4 (.html)" — those
seven were the `.qc.html` reports the app itself had written, sitting beside
the renders as the default routing mode intends. Reporting output as
unrecognised input every time is exactly the kind of line that trains people
to stop reading the log. Files matching `.qc.html` / `.qc.json` are now
excluded from that count.

**Also learned:** WestUrban_AUG_2026.mp4 ran more than ten minutes of QC
without finishing, which at the measured ~16s per minute implies 35-40 minutes
of video. Every clip measured until now has been 1-2 minutes. Long-form is a
different shape of job and the frame budget has never been exercised against
it.

**Tested:** 314 tests passing (up from 311).

### Session 18 — 2026-09-16

A brand film took **784 seconds to check 142 seconds of video** — about 330s of
QC per minute against a measured baseline of 16s. Twenty times slower, on a
clip barely longer than the interviews.

Investigated rather than guessed, and two hypotheses were tested and rejected:

- **Follow-up frame seeks.** The clip had 41 scene changes against 0-1 for the
  interviews, and those follow-ups are extracted one ffmpeg seek at a time. A
  cut-heavy long-GOP clip was built to reproduce it: 0.17s per seek, about 6s
  in total. Not it.
- **OCR on complex footage.** Tesseract's sparse-text mode hunts text
  everywhere, so a visually busy frame ought to cost more. Measured across
  plain, test-pattern and heavy-noise frames: 0.27s, 0.51s, 1.27s. Not it
  either.

That leaves a setting I chose myself. The launchd agent declares
`ProcessType = Background`, which is not merely a low priority — it is a
throttled class, with disk I/O held back as well as CPU, on the assumption the
work is not time-sensitive. The timings fit: the interviews ran at 08:30 on a
presumably idle machine, the brand film at 15:49 mid-afternoon on the main
suite. The intent (stay off the editor's cores) was right; the setting was too
blunt.

`[service] process_type` and `nice` are now configurable and default to
`Standard` with `nice = 5`, which yields to the editor without being throttled.

**Confirmed in Session 19.** The same file run through `bhqc scan` in Terminal,
outside the throttled service, took **111.9s against 784s** — a 7x difference
attributable to the ProcessType setting.

**Worth noting on the QC itself:** 41 scene changes and 151 frames produced no
text findings at all. The only flag was 2.18s of mid-programme silence, which
is a fair thing to raise. No false positives on cut-heavy graded material.

**Tested:** 315 tests passing.

### Session 19 — 2026-09-16

The throttling diagnosis holds: WestUrban run through `bhqc scan` in Terminal
took **111.9s against the service's 784s**. Seven times faster for identical
work, which is the ProcessType setting and nothing else.

A second, smaller factor is now visible in the same numbers. Per sampled frame:

    MMR interview     126.8s video,  85 frames,  33.3s QC   0.39 s/frame
    WestUrban         142.4s video, 151 frames, 111.9s QC   0.74 s/frame

Nearly twice the cost per frame, and nearly twice as many frames. Both trace to
the 41 scene changes: follow-up frames after a cut are grabbed by individual
ffmpeg seek at roughly 0.4s each, against 0.03s for a frame from the
single-pass grid, and cut-heavy graded footage gives Tesseract more to chew on.

At 112s for a 2m22s clip it is comfortably faster than real time, so this is
noted rather than acted on. The obvious lever — OCR is serial, and the machine
has cores to spare — is deliberately **not** being pulled without asking,
because it trades directly against the reason the service is niced in the first
place: staying out of the way of whoever is editing. Fast and greedy is not
obviously better than slow and invisible on a shared edit machine.

### Session 20 — 2026-09-16

**The first real error, and it was missed.** WestUrban contains a graphics
mistake at 02:12–02:13 — a super pops on, goes off, then appears properly a
couple of shots later. The QC passed it with one unrelated silence note.

This was not a tuning failure. Nothing in the app looked for it: the text is
spelled correctly, there is no black and no silence. The original spec listed
"flash frames" as an explicit v1 stretch item, so it was known to be out of
scope — but four clean batches had built a quiet confidence that this usefully
punctures. Not crying wolf and actually catching things are different
properties, and only the first had been demonstrated.

**Built a flashed-graphic detector.** It reuses the frames already sampled for
spell-checking, so it costs nothing extra: OCR text is collapsed into runs, and
a run of one frame is flagged when the same text appears again later in a run
of two or more. Either appearance alone is unremarkable — a brief one could be
sampling luck, a long one is just a super — so it is the pairing that makes it
a defect. A recurring brand tag held properly both times does not trigger it.

**A correction worth recording.** Chasing this, synthetic clips appeared to
show grid frame timestamps drifting by ~0.6s, which would have made every
timecode in every report wrong. Three rounds of investigation later, the fault
was the test harness: clips assembled with `concat` do not have the timing
their filter expressions imply, and ffmpeg's own accurate seek disagreed with
wall time on the ruler clip itself (t=59 reading as "second 64" in a 60-second
clip). The measured grid error of +0.55s sits inside the ±0.5s the ruler's
one-second granularity produces on its own. **There is no evidence of a
timestamp bug.** The flashed-graphic detector is therefore tested against
constructed frames rather than rendered video.

**Also:** sampled frames in the work folder are now named by the moment they
came from (`t00132.000.png`), so `--keep-work` can answer "what did QC actually
see at 02:12?" — which is the question this session needed and could not ask.

**Tested:** 327 tests passing (up from 315).

**What this changes about the project's status:** the false-positive rate is
good and well evidenced. The false-negative rate is now known to be non-zero
and remains unmeasured. That is the honest summary.

### Session 21 — 2026-09-16

`bhqc scan FILE --keep-work` failed with "unrecognized arguments". The flag was
declared only on the top-level parser, so argparse required
`bhqc --keep-work scan FILE` — which is how argparse works and not how anyone
types, least of all when following an instruction that put the flag last.

`--keep-work`, `--config`, `--root` and `--verbose` are now accepted on either
side of the subcommand. The subcommand copies use `argparse.SUPPRESS` as their
default, so a value given before the subcommand is not clobbered by the
subcommand's own default — the standard argparse trap, now covered by a test.

**Tested:** 331 tests passing (up from 327).

### Session 22 — 2026-09-16

**Shipped a crash in the flashed-graphic detector, caught on the first real
run.** `findings.append` was called before the list it appends to was created —
an UnboundLocalError that took the entire text detector down, so spell-checking
was skipped on that file too. The report said so plainly ("the text detector
failed to run... this file was not fully checked"), which is the one thing that
went right.

**Why 333 tests passed anyway, which is the part worth remembering.** The buggy
line sat inside a loop that only runs when a flash is actually found. No test
clip contained one, so the line never executed. Every test exercised the
*decision* to report and none exercised the *reporting*. Coverage of the happy
path is not coverage.

Fixed by extracting `flashed_graphic_findings()` as a function that can be
called with constructed frames, and testing it directly — including that the
finding survives `to_dict()`, since the report and JSON sidecar both go through
it.

**The crash is also evidence.** That code path only executes on a detection, so
WestUrban contains something the detector recognised as a flashed graphic. The
frames confirm the raw material was there: `t00130.500` through `t00139.500`
were sampled, including `t00132.000`, squarely inside the 02:12-02:13 window
the error was reported at.

**Tested:** 336 tests passing (up from 333).
