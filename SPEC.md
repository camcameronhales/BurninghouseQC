# Burninghouse QC — project spec and current state

**Purpose of this doc:** the standing description of what this is, what state
it is in, and what is genuinely unresolved. Written to be handed to a fresh
conversation with no other context.

**Repo:** `camcameronhales/BurninghouseQC`
**Branches:** there is no `main` — every branch is a feature branch and the
newest is the current state. `git branch -r` is the authority on that, not this
line.
**Status:** deployed and running on two machines. Detection for spelling, black
frames and audio dropouts is working and evidenced. Detection for mis-timed
graphics is **confirmed against the real case it was built for** (§5).

---

## 1. What it does

Watches a folder for rendered video. When a file lands and finishes writing, it
checks it and writes an HTML report next to it. The render is never moved,
renamed or altered.

| Check | Method | Fails when | Notes |
| --- | --- | --- | --- |
| On-screen spelling | frame sampling → Tesseract OCR → spell-check | confidently read (≥85%) in 2+ frames and not a word | several layers of false-positive control, see §4 |
| Black frames | FFmpeg `blackdetect` | ≥0.5s mid-programme | head/tail fades are `info` only |
| Audio dropout | FFmpeg `silencedetect` | ≥3s mid-programme, or a silent file | head/tail handles are `info` only |
| Mis-timed graphic | text timeline over sampled frames, then a denser second pass over each candidate | — always `review` | confirmed on the real case, see §5 |
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

**One true positive, on the one error known to exist.** The mis-timed graphic in
`WestUrban_AUG_2026.mp4` is caught (§5). That is the first evidence of a false
*negative* being closed rather than a false positive avoided, and it is a single
case: four clean batches plus one catch is not a false-negative rate.

**Still not measured: what it misses.** Nothing here says how many mistakes of
other kinds would pass. A graphic held under about three seconds cannot be
timed at all on the current 1.5s grid — see §6.

## 4. False-positive controls (all in `[spelling]` / `[text]`)

A word passes six gates before it can fail a file, in this order: OCR
confidence (`min_confidence = 70`), word shape (length, digits, short ALL-CAPS
acronyms, roman numerals, case), proper nouns, the word lists, repetition, and
then a higher confidence bar to fail rather than route to review
(`fail_confidence = 85`, `fail_min_occurrences = 2`).

Four of those were added in response to real failures rather than designed in,
and those are the ones worth knowing about:

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

**Control 4 did go too far, and is now off by default.** It was the only change
that reduced sensitivity rather than just cutting noise, and it cost a real
catch: "valuues" was burnt into a subtitle on a client deliverable and the file
came back with no spelling findings at all. A caption is on screen about two
seconds against a 1.5s grid, so it lands on one sampled frame, and two were
required before anything was said.

`text.report_min_occurrences` is now 1. A single sighting routes to *review*,
never *fail* — `fail_min_occurrences` is still 2 — so the cost is review noise
from animation fragments rather than a wrongly failed file. Set it back to 2 on
work with no subtitles in it.

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

**Resolved on 2026-09-17: it is caught.** The QC now returns REVIEW with
"A graphic appears briefly at 00:02:12.00, then again properly at 00:02:16.50".
`scripts/diagnose_frames.py` over the kept frames settled the open question —
OCR reads the card at **95–96% confidence** ("For More Information on fall
prevention visit worksafe.vic.gov.au"), so the text-based approach was sound
and pixel-difference is not needed.

The diagnostic also found a defect that had been shortening the measurement:
`frame_signature` required `.isalpha()`, so the URL was dropped and the three
frames showing only the URL had an *empty* signature — which ends a run. A card
on screen for over four seconds measured 0.70s. Domains now count toward a
frame's signature, and a brief appearance is measured in seconds
(`flash_max_span`) rather than counted in sampled frames.

**The diagnostic, kept because the next silent detector will need it:** it runs
OCR over the kept frames in a window and prints what was found in each.
`scripts/diagnose_frames.py` — point it at the `--keep-work` folder:

```bash
python scripts/diagnose_frames.py "$QC_ROOT/work/<job>" --from 129 --to 142
```

It separates the cases that look identical from outside: OCR reading nothing,
OCR reading it but the filters dropping it, both halves read but not pairing,
and the two appearances merging into one run because no blank frame was
sampled between them.

If the graphic's text does not appear at all, the detector cannot work and the
approach needs rethinking — likely toward pixel-difference comparison of the
lower-third region rather than OCR.

## 6. Known issues

- **Brief graphics are timed by a second pass, which is unproven on real
  footage.** The baseline grid cannot resolve below its own 1.5s interval, so
  once a brief appearance is found the detector re-samples around it at
  `flash_refine_interval` (0.25s) to find its real edges. Bounded by
  `flash_refine_max`, and costing frames per candidate rather than per job.
  It is covered by tests against a stubbed decoder, and has not yet run against
  a real deliverable — the WestUrban frames predate it.
- **Spell-check cannot see a real word in the wrong place.** The same subtitle
  read "resinate" for "resonate", and `resinate` is a word — a resin treatment.
  Subtitles are transcribed speech, where errors are overwhelmingly homophones
  and real-word slips (their/there, form/from, lead/led), and no dictionary
  catches any of them. On subtitles this narrows a human read; it does not
  replace one.
- **Subtitles on a separate track are invisible.** `ffprobe` is read for video
  and audio streams only, and frame extraction never burns subtitles in, so a
  soft subtitle or caption track is not checked and nothing says so. Only text
  burnt into the picture is read. Found when a deliverable with misspelled
  subtitles came back with no spelling findings at all.
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

The crash in §5 passed the entire suite — 333 tests at the time. The buggy line sat inside a loop that only
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
                    check-access / forget / install-service /
                    uninstall-service / update
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
  ffmpeg_tools.py   ffmpeg/ffprobe wrappers, and the build's filter list
  detectors/
    black.py        blackdetect
    silence.py      silencedetect
    text.py         sampling → OCR → spell-check + flashed graphics
  transfer.py       verified copy, used to stage a network file locally
  ledger.py         what has already been checked
  service.py        the launchd agent
  status.py         the JSON status file and log behind `bhqc status`
  notify.py         desktop banners
  access.py         permission verification — proves read-only is read-only
  mounts.py         network-share detection (dormant: local storage only)
  power.py          caffeinate while a job runs
docs/
  local-trial.md    the install and running protocol   ← start here
  service-setup.md  installing it as a launchd agent
  tuning.md         what to change when
  session-log.md    the full build diary
  server-safety.md  } dormant — kept for a possible future
  readonly-account.md }
scripts/
  make_sample.py    generates test footage with known faults
  diagnose_frames.py  OCR the kept frames in a window (see §5)
service/
  com.burninghouse.qc.plist   the launchd agent
tests/              the suite; see below
```

`pytest` runs the suite; `pytest -m "not ffmpeg"` skips the tests needing
FFmpeg and Tesseract installed. The count is deliberately not written down
here — it was wrong in this document twice before anyone noticed.

## 9. The skill

`.claude/skills/burninghouse-qc-diagnosis/` owns the tuning and diagnosis loop:
read a `.qc.html` or the kept frames, judge whether a finding is real, work out
why a miss was missed, and name the specific config change or dictionary entry.
The app itself stays a normal Python package — it does not need to be a skill to
be useful, and the skill does not wrap it.

`references/knobs.md` holds the symptom-to-setting table. Every default quoted
in it was checked against `config.py` when written; if the two drift, the code
is right and the table is stale.

The skill has not been run through an eval set. It was written from one long
diagnosis session rather than tested across many, so treat its coverage as
unproven in the same way §3 treats the app's.
