# Burninghouse QC

Unattended QC for rendered video. Watches a folder, checks each new render for
misspellings in on-screen graphics and for technical faults, writes a report,
and sorts the file into **pass**, **review** or **error**.

Built to run as a background service on a single edit machine — no UI, no
server, no login required.

```
input/                       pass/    Client_Spot_v4.mp4
  Client_Spot_v4.mp4  ──▶             Client_Spot_v4.qc.html
  Client_Spot_v3.mp4  ──▶    error/   Client_Spot_v3.mp4
                                      Client_Spot_v3.qc.html
```

Every file gets a report, whichever folder it lands in, so staff can always see
*why* it was routed there.

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
   and words under four letters are never checked.

British/Australian spellings are handled automatically: `colour`, `organise`,
`centre`, `programme` and the rest are accepted without needing a word list,
while genuine typos like `coulour` are still flagged. Brand names, client names
and jargon go in [`dictionary/custom_words.txt`](dictionary/custom_words.txt) —
a plain text file, one word per line, re-read on every job, no restart needed.

### Frame sampling

Sampling every frame of a 90-minute master would take longer than the render.
Instead the pipeline samples on a fixed interval (default every 2s) *and* adds
extra frames just after each detected scene change, because on-screen supers
almost always arrive on a cut. A hard ceiling (`max_frames`) keeps long
programmes bounded, thinning coverage evenly rather than giving up partway.

---

## Install

Needs **Python 3.11+**, **FFmpeg** and **Tesseract OCR** on `PATH`.

```bash
git clone https://github.com/camcameronhales/BurninghouseQC.git
cd BurninghouseQC
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .

bhqc init          # creates the QC folders and a config.toml
bhqc doctor        # checks FFmpeg, Tesseract, the dictionary and the folders
```

<details>
<summary>Getting FFmpeg and Tesseract</summary>

- **Windows** — `winget install Gyan.FFmpeg UB-Mannheim.TesseractOCR`, then
  reopen the terminal so `PATH` updates.
- **macOS** — `brew install ffmpeg tesseract`
- **Debian/Ubuntu** — `sudo apt install ffmpeg tesseract-ocr`
</details>

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
```

`scan` and `run` exit `0` on pass, `10` on review and `20` on fail, so they drop
straight into a render script or a scheduled task.

To start it automatically at boot, see
**[`docs/service-setup.md`](docs/service-setup.md)** — NSSM or Task Scheduler on
Windows, launchd on macOS.

### Knowing when a render has finished

A file appearing is not the same as a file being finished. Two guards run
together, so an in-progress render is never QC'd mid-write:

- files with in-progress extensions (`.tmp`, `.part`, …) are ignored outright,
  and the rename to the final name is what triggers the pick-up;
- everything else must report an identical size and mtime across three
  consecutive polls (~10s of quiet) before processing starts.

---

## Reports

Each file gets `<name>.qc.html` next to it — a single self-contained page with
the verdict, file metadata, and every finding with a timecode. Spelling flags
include the frame with the offending word boxed in red, embedded in the HTML
so the report survives being emailed or archived. It prints straight to PDF
from the browser if a PDF is wanted.

A `<name>.qc.json` sidecar carries the same data for scripting.

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
  ffmpeg_tools.py   ffmpeg/ffprobe wrappers
  detectors/
    black.py        blackdetect
    silence.py      silencedetect
    text.py         frame sampling -> OCR -> spell-check
scripts/make_sample.py   generates test footage with known faults
docs/                    service setup and threshold tuning
service/                 Windows .bat and macOS launchd plist
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
