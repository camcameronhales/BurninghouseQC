# Phase 1 — running it on this machine, local files only

This is the setup to do **now**: everything on the Mac's own drive, nothing
touching the Synology. The share comes later, once you're satisfied the QC
itself does what you want.

Target machine: macOS 26.5.2 (25F84). Budget 20 minutes, most of it waiting
for Homebrew.

---

## What you'll end up with

```
/Users/Shared/BurninghouseQC/
├── .venv/                  the Python environment
├── config.toml             all thresholds, absolute paths
├── dictionary/
│   └── custom_words.txt    brand and client names — yours to edit
└── qc_root/
    ├── input/              ← drop renders here
    ├── pass/               reports for clean files
    ├── review/             reports for borderline files
    ├── error/              reports for clear-cut problems
    ├── work/               scratch, cleared automatically
    ├── burninghouse-qc.log what it has done
    ├── status.json         what it's doing right now
    └── processed.json      what it has already checked
```

Renders stay in `input/`. The verdict folders get the **report** plus a
shortcut to the file. That's `report_only` mode — deliberately the same mode
you'll deploy to the Synology with, so phase 1 actually tests the thing you're
going to ship.

> Prefer the files physically sorted during the trial? Set
> `routing.mode = "move"` in `config.toml`. It's perfectly safe on a folder the
> app owns. Just know you'll then be trialling a different mode than you deploy.

---

## Step 1 — install FFmpeg and Tesseract

Open Terminal.

```bash
# Homebrew, if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install ffmpeg tesseract
```

On Apple Silicon, Homebrew tells you to add itself to your PATH at the end of
install. Do what it says, then close and reopen Terminal.

Check both are visible:

```bash
ffmpeg -version | head -1
tesseract --version | head -1
```

Two lines of version output means you're good.

FFmpeg 9 is newer than the version this was built against (6.x). The app picks
its flags based on the version it finds, so that is handled — but step 5 is the
check that proves it, which is why step 5 exists.

## Step 2 — install the app

```bash
sudo mkdir -p /Users/Shared/BurninghouseQC
sudo chown "$(whoami)" /Users/Shared/BurninghouseQC
cd /Users/Shared/BurninghouseQC

git clone https://github.com/camcameronhales/BurninghouseQC.git .
python3 -m venv .venv
.venv/bin/pip install -e .
```

`/Users/Shared` is deliberate: it isn't one of the folders macOS protects with
privacy permissions, so a background service can read it without you granting
Full Disk Access. Putting this in `~/Documents` or `~/Desktop` will cause
problems later.

## Step 3 — create the config and folders

```bash
.venv/bin/bhqc init
```

That writes `config.toml` with absolute paths, creates the folder tree, and
gives you your own `dictionary/custom_words.txt` — outside the repo, so
`git pull` can never overwrite words you've added.

## Step 4 — confirm the machine is ready

```bash
.venv/bin/bhqc -c config.toml doctor
.venv/bin/bhqc -c config.toml check-access
```

`doctor` checks FFmpeg, Tesseract, the dictionary and the folders.
`check-access` proves the permissions by actually trying them — it should end
with:

```
All good — the input folder and the QC folders are all usable.
```

If `doctor` says Tesseract is not usable, PATH is the culprit — revisit step 1.

## Step 5 — prove it works, before trusting it with your own work

The repo can generate a clip with faults deliberately planted in it: a
misspelled title card, a two-second black run, a five-second audio dropout.

```bash
.venv/bin/python scripts/make_sample.py /tmp/qc_test_faulty.mp4
.venv/bin/python scripts/make_sample.py /tmp/qc_test_clean.mp4 --clean

.venv/bin/bhqc -c config.toml scan /tmp/qc_test_faulty.mp4
.venv/bin/bhqc -c config.toml scan /tmp/qc_test_clean.mp4
```

The faulty one must report three failures — a misspelling of "Acheiving", a
sustained black run, and an audio dropout. **The clean one must report
nothing.** If the clean clip throws flags, stop and tell me before going
further; something is misconfigured.

## Step 6 — run it against your own renders

Copy a handful of finished renders — ideally a mix you already know the verdict
on, including at least one you know has a mistake in it — into the input folder:

```bash
cp ~/path/to/some_renders/*.mov /Users/Shared/BurninghouseQC/qc_root/input/
```

Then start the watcher in the foreground, so you can watch it work:

```bash
.venv/bin/bhqc -c config.toml watch
```

You'll see each file noticed, waited on, checked and filed. <kbd>Ctrl-C</kbd>
stops it.

Expect roughly **30 seconds of QC per minute of video**, so a 10-minute master
lands around 5 minutes. Each file also sits for ~15 seconds first while the app
confirms it's finished writing.

Then read the reports in `qc_root/pass`, `review` and `error` — double-click
the `.qc.html` files.

## Step 7 — tune, over a couple of weeks

Run it alongside your normal manual QC. Don't let it be the only gate yet.

The loop for any file where you disagree with it:

```bash
# see exactly what the OCR was looking at
.venv/bin/bhqc -c config.toml scan "/path/to/that_render.mov" --keep-work

# ...adjust one threshold in config.toml, then check the same file again
.venv/bin/bhqc -c config.toml forget "/path/to/that_render.mov"
```

`--keep-work` leaves the sampled frames in `qc_root/work/` so you can see the
actual frame a flag came from. `forget` clears a file from the ledger so the
watcher will re-check it (`forget --all` resets everything).

Two things worth doing early:

- **Add brand and client names** to `dictionary/custom_words.txt` as they come
  up. One per line. Takes effect on the next file, no restart. This is the
  single highest-value tuning action — most false positives are proper nouns.
- **Time a real master**: `time .venv/bin/bhqc -c config.toml scan "a_typical_render.mov"`.
  All my timing figures come from synthetic footage on a Linux container; your
  numbers are the ones that matter.

`docs/tuning.md` is the full table of what to change when.

## Step 8 — only once you're happy: run it automatically

Skip this until the verdicts are ones you'd act on. While you're tuning,
running `bhqc watch` by hand is easier.

When you're ready, `docs/service-setup.md` installs it as a launchd agent that
starts on its own and survives reboots.

## Step 9 — later still: point it at the Synology

Only after phase 1 has earned your trust. `docs/server-safety.md` explains what
the app does and doesn't write, and `docs/readonly-account.md` sets up a
read-only account so the guarantee is enforced by the NAS rather than promised
by the config.

The change itself is one line — `paths.input` — because the mode you'll be
running is the one you've been trialling all along.

---

## Quick reference

| | |
| --- | --- |
| Check one file, change nothing | `bhqc -c config.toml scan FILE` |
| Check one file and file it | `bhqc -c config.toml run FILE` |
| Watch the input folder | `bhqc -c config.toml watch` |
| What's it doing right now | `bhqc -c config.toml status` |
| Is the machine set up | `bhqc -c config.toml doctor` |
| Are the permissions right | `bhqc -c config.toml check-access` |
| Re-check a file | `bhqc -c config.toml forget FILE` |
| Re-check everything | `bhqc -c config.toml forget --all` |
| What has it done | `cat qc_root/burninghouse-qc.log` |

Exit codes: `0` pass, `10` review, `20` fail — handy if you ever script it.

## If something goes wrong

**"command not found: bhqc"** — use the full path,
`/Users/Shared/BurninghouseQC/.venv/bin/bhqc`, or activate the venv first with
`source .venv/bin/activate`.

**Tesseract "not usable" in doctor** — Homebrew isn't on PATH. Reopen Terminal,
or run `eval "$(/opt/homebrew/bin/brew shellenv)"`.

**"Unrecognized option" from ffmpeg** — a flag has been removed in a newer
FFmpeg than this was tested on. `bhqc ... doctor` prints the exact version;
send me that line and the error.

**A file sits in input and nothing happens** — it must be `.mov` or `.mp4`
(add others to `watcher.video_extensions`), and it must have been unchanged for
~15 seconds. Check it isn't in `processed.json` already: `bhqc ... forget FILE`.

**Every file comes back with spelling flags** — the dictionary path is probably
wrong. `bhqc ... doctor` prints the path and the word count; it should say 20+
words, not "FILE MISSING".

**It's slower than the render** — see the runtime section of `docs/tuning.md`.
Raise `text.sample_interval` first.
