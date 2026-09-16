# Burninghouse QC — project spec and current state

**Purpose of this doc:** the standing description of what this is, what state
it is in, and what is genuinely unresolved. Written to be handed to a fresh
conversation with no other context.

**Repo:** `camcameronhales/BurninghouseQC`
**Branch:** `claude/video-qc-app-spec-radpoh` (all work; `main` is empty)
**Status:** deployed and running on two machines. Detection for spelling, black
frames and audio dropouts is working and evidenced. Detection for mis-timed
graphics is new, and **not yet verified against the real case it was built for.**

---

## 1. What it does

Watches a folder for rendered video. When a file lands and finishes writing, it
checks it and writes an HTML report next to it. The render is never moved,
renamed or altered.

| Check | Method | Fails when | Notes |
| --- | --- | --- | --- |
| On-screen spelling | frame sampling → Tesseract OCR → spell-check | confidently read (≥85%) in 2+ frames and not a word | four layers of false-positive control, see §4 |
| Black frames | FFmpeg `blackdetect` | ≥0.5s mid-programme | head/tail fades are `info` only |
| Audio dropout | FFmpeg `silencedetect` | ≥3s mid-programme, or a silent file | head/tail handles are `info` only |
| Mis-timed graphic | text timeline over sampled frames | — always `review` | **unverified, see §5** |
| Unreadable file | `ffprobe` | cannot open, or zero duration | |

Verdicts are **pass / review / fail**, stated in the report. Every file gets a
report regardless of verdict.

## 2. How it is deployed

Two Macs, each a completely independent install against its **own local
storage**. Whichever machine did the render does its own QC. Nothing is shared;
neither touches the Synology.

```
/Users/Shared/BurninghouseQC/          the install (git checkout + .venv)
  config.toml                          all thresholds, absolute paths
  dictionary/custom_words.txt          brand/talent names — hand-maintained
  qc_root/                             work, logs, status, ledger
/Users/Shared/BurninghouseQC Check/    ← renders go here, reports appear beside
```

Runs as a **launchd agent** (`com.burninghouse.qc`), starting at login and
restarting on crash. macOS 26.5.2, Homebrew FFmpeg 9.0.1, Tesseract 5.5.3,
Python 3.13 from Homebrew (the system 3.9 cannot run it).

**The update rule, which matters:** a launchd agent holds the code it started
with, so `git pull` alone changes nothing about what is running. Always
`bhqc update`, which pulls and restarts together and refuses to interrupt a
job in progress.

Full install walkthrough: `docs/local-trial.md`.

## 3. What is proven, and what is not

**Proven on real client work.** Five MMR executive interview deliverables
(1080p, 1m39s–2m15s) all return clean PASS with only informational notes about
tail handles. Getting there took three rounds of false-positive work: surnames
in lower thirds, edge silence, and fragments of words caught mid-animation.

**Runtime, measured.** ~16 seconds of QC per minute of 1080p on an unloaded
machine. Cut-heavy material costs roughly 3× that — 41 scene changes means many
more sampled frames and many more of them fetched by individual seek.

**Not proven: that it catches anything.** This is the crux. Four clean batches
demonstrate it does not cry wolf. They demonstrate nothing about false
negatives. The one real error known to exist in the test material — see §5 — was
missed by the original build and is still not confirmed caught.

## 4. False-positive controls (all in `[spelling]` / `[text]`)

Added in response to real failures, in order:

1. **British/Australian spellings** — `variants.py` transforms a suspect word to
   its US form and re-checks, so `colour` passes while `coulour` still fails.
   The bundled dictionary is US English.
2. **Case shape** — only lowercase, Title Case and ALL CAPS are checked.
   `gOLOUR` is a misread `C`, not a misspelling.
3. **Proper nouns** — a Title-case word beside another Title-case word is taken
   as a name. A spell-checker cannot validate a surname, and every interview
   was failing on the talent's name.
4. **Repetition** — a word must appear in ≥2 sampled frames to be reported at
   all. Titles animate on, and a frame caught mid-wipe reads the half-revealed
   super as a word (`nson` from "Branson", `offic` from "office").

**Control 4 is the one that may have gone too far.** It is the only change that
reduces sensitivity rather than just cutting noise: a misspelling on a card
shown for under ~3 seconds can now be missed entirely.
`text.report_min_occurrences = 1` reverts it.

## 5. The open problem

`WestUrban_AUG_2026.mp4` (2m22s) contains a real graphics error at **02:12–02:13**:
a super pops on, goes off, and appears properly a couple of shots later. The QC
passed it clean.

A **flashed-graphic detector** was built for this. It collapses OCR text into
runs over time and flags a one-frame appearance whose text recurs later in a
sustained run — either half alone is unremarkable, the pairing is the defect.

Where it stands:

- Frames **were** sampled inside the window (`t00130.500` … `t00139.500`,
  including `t00132.000`), so the raw material exists.
- Opening `t00132.000.png` shows the graphic on screen.
- The first run **crashed** — `findings.append` before the list was created,
  an UnboundLocalError that took the whole text detector down. Fixed.
  Notably, that path only executes on a detection, which suggested the detector
  had fired.
- The most recent run, post-fix, **does not flag it**.

So it remains unresolved. Plausible causes, untested:

- OCR cannot read that particular graphic (style, contrast, size), so there is
  no text to correlate. Check by OCRing `t00132.000.png` directly.
- The text at 02:12 does not overlap enough with the proper appearance to match
  (`flash_match = 0.6`).
- The proper appearance is fewer than `flash_min_proper_frames = 2` sampled
  frames.
- The premise is wrong and a text-based approach cannot see this at all — the
  graphic may be recognised by OCR in neither appearance.

**The diagnostic that settles it:** run OCR over the kept frames from 129s–142s
and print what text is found in each. If the graphic's text does not appear,
the detector cannot work and the approach needs rethinking — likely toward
pixel-difference comparison of the lower-third region rather than OCR.

## 6. Known issues

- **Flashed-graphic detection unverified** (§5). The feature is on by default
  and currently silent on the one case it exists for.
- **Custom dictionary does not sync** between the two machines. Hand-copied;
  currently unedited (20 shipped entries) on both.
- **`ProcessType` fix may not be applied.** The launchd agent originally
  declared `ProcessType = Background`, a *throttled* class that made one job 7×
  slower (784s vs 112s for the same file). Fixed in code, but it requires
  `bhqc install-service --force` plus a bootout/bootstrap on **each** machine,
  and that has not been confirmed done on either.
- **Cut-heavy material is ~3× slower** per minute than interviews. Acceptable
  at current lengths, untested on long-form.
- **OCR is single-threaded.** Parallelising would cut runtime several-fold but
  trades against the reason the service is niced at all — on a shared edit
  machine, slow and invisible may beat fast and greedy. Deliberately not done.

## 7. A lesson worth carrying forward

The crash in §5 passed 333 tests. The buggy line sat inside a loop that only
runs when the detector finds something, and no test fixture contained a flash —
so every test exercised the *decision* to report and none exercised the
*reporting*. When adding a detector, test the finding it produces, not only the
condition that triggers it.

Similarly, roughly three sessions were lost to an apparent frame-timestamp drift
that turned out to be the test harness: synthetic clips assembled with FFmpeg
`concat` do not have the timing their filter expressions imply. There is no
timestamp bug. Do not re-investigate it without a trustworthy reference.

## 8. Code map

```
burninghouse_qc/
  cli.py            scan / run / watch / init / status / doctor /
                    check-access / forget / install-service / update
  config.py         every tunable, loaded from config.toml
  watcher.py        watchdog → queue → single worker
  stability.py      deciding when a render has finished writing
  pipeline.py       runs the detectors, assembles the result
  findings.py       Finding, Severity, the pass/review/fail rule
  router.py         where the report goes; "alongside" is the default
  report.py         the self-contained HTML report
  spelling.py       dictionary + OCR-aware filtering + proper nouns
  variants.py       British/Australian spelling tolerance
  scan.py           one decode pass shared by black + scene detection
  detectors/
    black.py        blackdetect
    silence.py      silencedetect
    text.py         sampling → OCR → spell-check + flashed graphics
  transfer.py       verified copy/move (unused in the default mode)
  ledger.py         what has already been checked
  service.py        the launchd agent
  notify.py         desktop banners
  mounts.py         network-share detection (dormant: local storage only)
  power.py          caffeinate while a job runs
docs/
  local-trial.md    the install and running protocol   ← start here
  tuning.md         what to change when
  session-log.md    the full build diary
  server-safety.md  } dormant — kept for a possible future
  readonly-account.md }
```

336 tests. `pytest` runs them all; `pytest -m "not ffmpeg"` skips those needing
FFmpeg and Tesseract installed.

## 9. If picking this up as a skill

The natural shape is a skill that owns the *tuning and diagnosis* loop rather
than the app: read a `.qc.html` or the kept frames, judge whether a finding is
real, and propose the specific config change. The app itself is a normal Python
package and does not need to be a skill to be useful.

The first task for whoever picks this up is §5 — and the honest first question
is whether OCR can see that graphic at all, because everything downstream
assumes it can.
