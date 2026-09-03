# What this app does to your server

Renders land on a shared server, so the honest answer to "what can this thing
do to my files?" needs to be short, specific and checkable. Here it is.

## The short version

**The app never moves, renames, alters or deletes a render.** That holds in
every mode, and is the guarantee that actually matters.

What differs between modes is *where the report goes*:

- **`alongside`** (the default) writes `<name>.qc.html` next to the render.
  On a shared server that is a write to the server — a few hundred KB of HTML
  per file, in the same folder as the render.
- **`report_only`** writes nothing beside the render. Reports are filed in
  separate pass/review/error folders and the watched folder is never written
  to at all.

**For the Synology, this is a decision to make.** `alongside` is more
convenient — the report travels with the file and anyone opening the folder can
see it. `report_only` keeps the share strictly read-only. Both leave the
renders themselves untouched. If you want the share read-only at the
filesystem level (see [`readonly-account.md`](readonly-account.md)), you must
use `report_only`; `bhqc check-access` fails loudly if the mode and the
permissions disagree.

## Every operation the app performs on a source render

| Operation | When | Where it writes |
| --- | --- | --- |
| `stat()` — size and mtime | while waiting for the render to finish writing | nothing |
| open + read | FFmpeg decoding for QC | nothing |
| copy to local scratch | if the input folder is on a network mount | the *local* work folder |
| write report | after QC | beside the render (`alongside`) or the verdict folder (`report_only`) |
| write symlink | after QC, optional, verdict-folder modes only | the pass/review/error folder |

There is no other code path that touches the source. The one function that can
delete a source file is `transfer.safe_move`, and it is unreachable unless you
set `routing.mode = "move"` yourself.

You can verify that claim rather than take it on trust:

```bash
grep -rn "unlink\|shutil.move\|os.replace\|write_text" burninghouse_qc/
```

Everything that comes back writes into a QC-owned folder, except `safe_move`.

## The three modes

```toml
[routing]
mode = "report_only"    # the default
```

### `report_only` — the safe one

The render is left exactly where it is. The report goes to the verdict folder,
alongside a symlink pointing back at the original so staff can open the file
from the same place they read the report.

- **Server writes:** none.
- **Storage cost:** a few hundred KB of report per file.
- **The catch:** nothing about the input folder changes when a file is done, so
  the app keeps a ledger (`qc_root/processed.json`) of what it has already
  checked. Without it, a service restart would re-QC the entire folder.

### `copy` — a filed copy, original untouched

A verified copy of the render lands in the verdict folder; the original stays
put. Use this if you want a self-contained "failed QC" pile without touching
the server.

- **Server writes:** none. **Storage cost:** doubles.

### `move` — relocates the render

**Only point this at a QC folder the app owns outright.** Never at shared
storage where other people or systems expect files to stay put.

Even here the transfer is defensive. A move within one filesystem is a single
atomic rename. A move across filesystems — which any move off a share is —
copies to a temporary `.qc-partial` name, verifies the size (and the checksum
if `verify_hash = true`), atomically renames it into place, and only then
deletes the original. If anything fails, the partial file is removed and the
source is left untouched.

## The safeguards that apply in every mode

- **Preflight space check.** A copy that would not fit is refused before a byte
  is written, with 5% headroom. Filling a shared volume is its own outage.
- **Never overwrite.** A name that is already taken becomes `name (1)`, for the
  report as well as the render, so a second render of the same name cannot
  destroy the first one's report.
- **Rewrite detection.** The app snapshots size and mtime before QC starts. If
  the file changed by the time QC finishes — someone re-rendered over the top —
  the file is left alone and the report is flagged as describing the earlier
  version. It does not move a file it no longer understands.
- **Nothing is written next to a render.** `bhqc scan` writes its report to
  `qc_root/reports/`, not beside the source.
- **The stability check.** A render is only opened once its size and mtime have
  been unchanged across several polls, so the app never reads a half-written
  file. Files with in-progress extensions (`.tmp`, `.part`) are ignored outright.

## Load on the server

QC reads the file more than once — once for the black/scene detection pass and
again for frame extraction. Across a share that is several full reads, plus a
file handle held open for minutes.

So when the input folder is on a network mount, the app copies the render to
local scratch once and analyses that instead, deleting the scratch copy
afterwards. One read instead of three, and no long-lived handle on the server.

```toml
[routing]
work_from_local_copy = true      # the default
max_local_copy_gb    = 25.0      # bigger than this, read in place
```

It falls back to reading in place if local disk is short. A 10-minute 1080p
master is a few GB, so a modest scratch allowance covers the house case.

## Rollout

**This document is about phase 2.** Phase 1 is entirely local and does not
involve the server at all — see **[`local-trial.md`](local-trial.md)**.

**Phase 1 — local only.** Renders on the Mac's own drive, in a folder the app
owns. Prove the QC does what you want and tune the thresholds. Nothing touches
the NAS. This is where the tool earns trust, and it is where the churn belongs:
re-running the same files with `--keep-work`, clearing the ledger, changing
thresholds.

**Phase 2 — read-only against the Synology.** Change one line — `paths.input` —
and point it at the share. `mode = "report_only"` is unchanged, because phase 1
was run in the same mode, so nothing about the app's behaviour is new. Add a
read-only account ([`readonly-account.md`](readonly-account.md)) so the NAS
enforces what the config already promises. Run alongside the manual QC and
compare verdicts.

**Phase 3 — only if you want it.** Once the verdicts are trusted you *could*
use `copy` for a self-contained failed-QC pile. There is no strong reason to
ever use `move` against shared storage, and the default will stay `report_only`.

## Permissions: belt and braces

The strongest guarantee isn't in the config, it's in the filesystem. If the
account the launchd agent runs as has **read-only** access to the share, then
no bug and no misconfiguration can write to it.

That is worth doing regardless of what this document promises, and it is the
one safeguard that doesn't depend on this app behaving.
**[`readonly-account.md`](readonly-account.md)** is the step-by-step: creating
the account (Synology, QNAP, Windows, macOS sharing, Samba, NFS), mounting it
read-only on the Mac with keychain credentials, and verifying it.

Verification is a command, not a promise:

```bash
bhqc -c config.toml check-access
```

It tries the operations rather than reading the config — a zero-byte probe file
created and immediately deleted in each folder, which is how a read-only share
is confirmed to actually be read-only. It also catches a config that asks for
something the permissions forbid, like `mode = "move"` against a read-only
share, and exits non-zero so it can gate a setup script.

## Checking what it did

```bash
bhqc -c config.toml doctor        # prints the routing mode and its consequence
```

```
Routing mode: report_only — renders are never touched; only the report is written
```

Every job also logs which of the three things happened to the source:

```
QC finished: Client_Master_v1.mov -> FAIL (3 fail, 0 review) in 8.1s
             | source left in place | report: .../error/Client_Master_v1.qc.html
```
