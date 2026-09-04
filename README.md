# Burninghouse QC

Unattended QC for rendered video. Watches a folder, checks each new render for
misspellings in on-screen graphics and for technical faults, writes a report,
and sorts the file into **pass**, **review** or **error**.

Built to run as a launchd background service on a macOS edit machine — no UI,
no server, nobody watching it. Each machine runs its own independent install
against its own local storage: whichever machine did the render does its QC.

```
renders/
  Client_Spot_v3.mp4
  Client_Spot_v3.qc.html   ← FAIL: misspelling at 00:01:12, black at 00:04:03
  Client_Spot_v4.mp4
  Client_Spot_v4.qc.html   ← PASS
```

Every file gets a report and the report sits next to the file it describes.
Nothing is moved, renamed or sorted into folders — the verdict is inside the
report, which gets read either way.

> **Setting this up? Start with
> [`docs/local-trial.md`](docs/local-trial.md)** — the step-by-step for
> running it on one machine against local files.
>
> **Your renders are never moved, renamed or altered.** The app reads each one
> and writes a report beside it. The only thing it ever adds to the folder is
> that `.qc.html` file. If even that is unwanted — pointing it at storage you
> do not own, say — `routing.mode = "report_only"` files reports elsewhere and
> touches nothing; see [`docs/server-safety.md`](docs/server-safety.md).

---

## What it checks

| Check | How | Fails when | Sent to review when |
| --- | --- | --- | --- |
| **On-screen spelling** | frames sampled → Tesseract OCR → spell-check | word is confidently read (≥85%) in 2+ frames and isn't a word | read is uncertain, or seen in only one frame |
| **Black frames** | FFmpeg `blackdetect` | ≥0.5s of black mid-programme | short flashes, or black at the head/tail (fades) |
| **Audio dropout** | FFmpeg `silencedetect` | ≥3s of silence mid-programme, or a silent file | short gaps, or silence at the head/tail |
| **Unreadable file** | `ffprobe` | FFmpeg can't open it, or duration is zero | — |

Every threshold in that table is a first guess and lives in `config.toml`.
See [`docs/tuning.md`](docs/tuning.md) for how to adjust them during the pilot.

### Spelling: the false-positive problem

OCR over graphics is the part most likely to cry wolf, so flags have to clear
three gates before they can fail a file:

1. **OCR confidence** — anything Tesseract read below 70% is discarded outright.
2. **Repetition** — a word has to appear in at least two sampled frames to fail.
   A one-frame sighting can only ever route to *review*.
3. **Word shape** — tokens with digits, short all-caps acronyms, roman numerals
   and words under four letters are never checked. Nor are odd case shapes
   (`gOLOUR`, `PROFESSlONAL`), which are misread characters rather than
   misspellings.
4. **Names** — a Title-case word beside another Title-case word is taken as a
   name in a lower third and skipped. A spell-checker cannot validate a
   surname, and flagging one would fire on every interview ever shot.

Fades to black and silent handles at the head and tail are recorded in the
report as information, but do not affect the verdict — they are on nearly every
deliverable.

British/Australian spellings are handled automatically: `colour`, `organise`,
`centre`, `programme` and the rest are accepted without needing a word list,
while genuine typos like `coulour` are still flagged. Brand names, client names
and jargon go in [`dictionary/custom_words.txt`](dictionary/custom_words.txt) —
a plain text file, one word per line, re-read on every job, no restart needed.

### Frame sampling and runtime

Sampling every frame would take longer than the render. The pipeline samples on
a fixed interval (default every 1.5s) *and* adds extra frames just after each
detected scene change, because supers almost always arrive on a cut. 1.5s is
chosen so that any card held for 3s or more is sampled at least twice, which is
what a misspelling needs before it can fail a file rather than route to review.

House deliverables run **2–10 minutes**, which fits the full cadence inside the
`max_frames` budget. Longer clips widen the interval rather than losing
coverage at the end of the programme.

Measured on real 1080p interview footage on the target Mac: **~16 seconds of QC
per minute of video**, so a 10-minute master lands around 2m 40s. Three things
keep it there — black and scene detection share a single decode pass, the whole
baseline grid is pulled in one more pass instead of one seek per frame, and
frames are normalised to ~1440px tall before OCR rather than blindly upscaled
2x. Together those took a 5-minute clip from 244s to 160s while sampling *more*
frames than before.

(Earlier synthetic benchmarks on a Linux container were about twice that; the
real machine and real footage are both faster.)

---

## Install

Needs **Python 3.11+**, **FFmpeg** and **Tesseract OCR** on `PATH`.

```bash
brew install ffmpeg tesseract          # Linux: sudo apt install ffmpeg tesseract-ocr

git clone https://github.com/camcameronhales/BurninghouseQC.git
cd BurninghouseQC
python3 -m venv .venv && . .venv/bin/activate
pip install -e .

bhqc init          # creates the QC folders and a config.toml
bhqc doctor        # checks FFmpeg, Tesseract, the dictionary and the folders
bhqc check-access  # proves the account is read-only on the renders share
```

## Use

```bash
# Check one file without touching it — the fastest way to sanity-check settings
bhqc scan /path/to/render.mov

# Check one file and sort it into pass/review/error
bhqc run /path/to/render.mov

# Run the service: watch the input folder until stopped
bhqc -c config.toml watch

# Is it alive? What did it last do?
bhqc -c config.toml status

# Prove the permissions are what you think they are
bhqc -c config.toml check-access

# Update the code AND restart the background service, in one step
bhqc -c config.toml update
```

> **`bhqc update`, not `git pull`.** Once the background service is installed it
> holds the code it started with, so a plain `git pull` updates the files and
> changes nothing about what is running — silently. `update` does both.

`scan` and `run` exit `0` on pass, `10` on review and `20` on fail, so they drop
straight into a render script or a scheduled task.

First time? **[`docs/local-trial.md`](docs/local-trial.md)** is the full
step-by-step. To install it as a launchd agent that starts on its own, see
**[`docs/service-setup.md`](docs/service-setup.md)**. That doc also covers the
three macOS things that catch people out: launchd not inheriting your `PATH`,
Full Disk Access for protected folders, and FSEvents not firing on SMB shares.

### Knowing when a render has finished

A file appearing is not the same as a file being finished. Two guards run
together, so an in-progress render is never QC'd mid-write:

- files with in-progress extensions (`.tmp`, `.part`, …) are ignored outright,
  and the rename to the final name is what triggers the pick-up;
- everything else must report an identical size and mtime across three
  consecutive polls (~10s of quiet) before processing starts.

Only `.mov` and `.mp4` are picked up by default — the house delivery formats.
Anything FFmpeg can decode works; add its extension to `watcher.video_extensions`.

### What happens to the file itself

Set by `routing.mode`:

| Mode | Where the report goes | The render | Use it for |
| --- | --- | --- | --- |
| **`alongside`** (default) | next to the render | untouched | the normal case |
| `report_only` | a `pass`/`review`/`error` folder | untouched, nothing written beside it | a folder that must stay untouched |
| `copy` | verdict folder | original stays, verified copy filed | a self-contained failed-QC pile |
| `move` | verdict folder | relocated | a QC folder this app owns outright |

Even `move` is defensive: same-filesystem moves are a single atomic rename, and
cross-filesystem moves copy to a `.qc-partial` name, verify the size (and
checksum, optionally), rename into place, and only then delete the original. A
failed transfer always leaves the source intact.

Three other guards apply in every mode: a preflight free-space check, a
never-overwrite rule that suffixes `name (1)` for reports as well as renders,
and a rewrite check — if the file changed while QC was running, it's left alone
and the report says so.

### On a Mac specifically

- **Network shares.** FSEvents doesn't fire for SMB or AFP mounts, so a render
  dropped on a NAS would never be noticed. The watcher detects a network mount
  and switches to polling by itself.
- **Sleep.** A `caffeinate` assertion is held for the duration of each job, so
  the machine can't doze off mid-QC — and released while idle, so it still
  sleeps normally between renders.
- **Finder noise.** `.DS_Store` and `._` AppleDouble forks are ignored.

---

## Reports

Each file gets `<name>.qc.html` beside it — a single self-contained page with
the verdict, file metadata, and every finding with a timecode. Spelling flags
include the frame with the offending word boxed in red, embedded in the HTML
so the report survives being emailed or archived. It prints straight to PDF
from the browser if a PDF is wanted.

Set `report.write_json = true` for a `.qc.json` sidecar carrying the same data,
if something needs to consume it. Off by default. Set
`report.verdict_in_filename = true` to get `Spot [FAIL].qc.html` instead, so a
folder listing can be triaged without opening anything.

---

## Layout

```
burninghouse_qc/
  cli.py            scan / run / watch / init / status / doctor
  config.py         every tunable, loaded from config.toml
  watcher.py        the service: watchdog -> queue -> single worker
  stability.py      deciding when a render has finished writing
  pipeline.py       runs the detectors, assembles the result
  findings.py       Finding, Severity and the pass/review/fail rule
  router.py         moves the file and its report into the right folder
  report.py         the self-contained HTML report
  spelling.py       dictionary + custom word list + OCR-aware filtering
  variants.py       British/Australian spelling tolerance
  scan.py           one decode pass shared by black + scene detection
  access.py         permission verification — proves read-only is read-only
  transfer.py       verified copy/move; never deletes an unverified source
  ledger.py         what has already been checked, so nothing is re-QC'd
  mounts.py         network-share detection (FSEvents vs polling)
  power.py          caffeinate assertion held only while a job runs
  ffmpeg_tools.py   ffmpeg/ffprobe wrappers
  detectors/
    black.py        blackdetect
    silence.py      silencedetect
    text.py         frame sampling -> OCR -> spell-check
scripts/make_sample.py   generates test footage with known faults
docs/                    local trial, service setup, tuning, server safety
service/                 macOS launchd plist
```

## Tests

```bash
pip install -e ".[dev]"
pytest                       # everything
pytest -m "not ffmpeg"       # skip tests that need FFmpeg/Tesseract installed
```

The end-to-end tests render synthetic clips with a deliberately misspelled
title card, a black run and an audio dropout, then assert the pipeline finds
exactly those — and that a clean clip comes back with **no** findings at all.
That last test is the false-positive guard; if it ever fails, the OCR
thresholds have drifted.

## Not in v1

Multi-machine rollout, auto-update, a GUI dashboard, and cloud OCR. Frozen
frame detection, loudness and resolution checks are stubs-to-be — the detector
interface takes a new check in one file.

Windows was scoped out once the edit machine was confirmed as a Mac. Nothing in
the app is macOS-only; only the service wrapper would need writing.
