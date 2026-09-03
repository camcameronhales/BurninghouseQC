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
    ├── input/              ← drop renders here; reports appear beside them
    ├── work/               scratch, cleared automatically
    ├── burninghouse-qc.log what it has done
    ├── status.json         what it's doing right now
    └── processed.json      what it has already checked
```

After a run, `input/` looks like this — each render with its report beside it:

```
Client_Spot_v3.mp4
Client_Spot_v3.qc.html
Client_Spot_v4.mp4
Client_Spot_v4.qc.html
```

Nothing is moved, renamed or sorted. The verdict is inside the report, which
gets read either way.

> Want the verdict visible without opening anything? Set
> `report.verdict_in_filename = true` and reports are named
> `Client_Spot_v3 [FAIL].qc.html`.
>
> Want reports kept out of the render folder entirely? Set
> `routing.mode = "report_only"` and they are filed in `pass/`, `review/` and
> `error/` instead, leaving the watched folder untouched.

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
```

**Use Homebrew's Python, not the one macOS ships.** macOS includes Python 3.9,
which `python3` resolves to by default. This app needs 3.11+ (it uses
`tomllib`), and 3.9's ancient bundled pip can't do editable installs either.

```bash
brew install python@3.13

/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e .
```

Confirm you got the right one — this must say 3.11 or higher:

```bash
.venv/bin/python --version
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

**The watcher takes over that Terminal window** while it runs. To add files
while it's watching, open a second window with <kbd>⌘N</kbd>. Or simpler for a
first run: copy the files in first, then start it — anything already sitting in
the folder is picked up at start-up.

It tells you what it found on start-up: how many files it queued, how many it
is ignoring because they are not `.mov` or `.mp4`, and how many it has already
checked. If it says the folder is empty, it is.

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

## Step 8 — run it automatically, no Terminal window

Two commands:

```bash
.venv/bin/bhqc -c config.toml install-service
```

That writes a launchd agent with the right paths filled in — the venv's Python,
your config, your folders — and prints the command to start it. Then run the
`launchctl bootstrap` line it gives you.

From then on it runs in the background, starts itself at login, and restarts if
it ever crashes. No Terminal window needed.

| | |
| --- | --- |
| Is it running? | `launchctl print gui/$(id -u)/com.burninghouse.qc \| head -20` |
| What's it doing? | `bhqc -c config.toml status` |
| Watch it work | `tail -f qc_root/burninghouse-qc.log` |
| Restart after a config change | `launchctl kickstart -k gui/$(id -u)/com.burninghouse.qc` |
| Stop it | `launchctl bootout gui/$(id -u)/com.burninghouse.qc` |
| Remove it | `bhqc uninstall-service` |

A threshold or dictionary change takes effect on the next file with no restart.
Only a path change needs the `kickstart`.

`docs/service-setup.md` has the detail, including running it without a login
and the privacy-permission traps.

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

**"File setup.py or setup.cfg not found" / "editable mode requires setuptools"**
— the venv was built with macOS's Python 3.9. Delete and rebuild it with
Homebrew's: `rm -rf .venv && brew install python@3.13 && /opt/homebrew/bin/python3.13 -m venv .venv`,
then upgrade pip and reinstall as in step 2.

**"command not found: bhqc"** — use the full path,
`/Users/Shared/BurninghouseQC/.venv/bin/bhqc`, or activate the venv first with
`source .venv/bin/activate`.

**Tesseract "not usable" in doctor** — Homebrew isn't on PATH. Reopen Terminal,
or run `eval "$(/opt/homebrew/bin/brew shellenv)"`.

**"Unrecognized option" from ffmpeg** — a flag has been removed in a newer
FFmpeg than this was tested on. `bhqc ... doctor` prints the exact version;
send me that line and the error.

**"No such filter"** — the FFmpeg build is missing something. `bhqc ... doctor`
lists which of the required filters are absent; `brew reinstall ffmpeg` gets a
full build. Note the QC pipeline itself needs only blackdetect, silencedetect,
select, metadata and fps — all present in every normal build.

**A file sits in input and nothing happens** — it must be `.mov` or `.mp4`
(add others to `watcher.video_extensions`), and it must have been unchanged for
~15 seconds. Restart the watcher and read its start-up lines: it says how many
files it queued, how many it ignored and why, and how many it had already
checked (`bhqc ... forget FILE` re-checks one).

**Every file comes back with spelling flags** — the dictionary path is probably
wrong. `bhqc ... doctor` prints the path and the word count; it should say 20+
words, not "FILE MISSING".

**It's slower than the render** — see the runtime section of `docs/tuning.md`.
Raise `text.sample_interval` first.
