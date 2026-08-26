# Running Burninghouse QC unattended

The QC app is a long-running process (`bhqc watch`) that needs to come back on
its own after a reboot or a power cut, on a machine nobody is logged into.
Pick the section for the edit machine's OS.

> **Open question from the spec (§6):** the edit machine's OS was never
> confirmed, so both are covered here. Only one of these needs doing.

---

## Prerequisites (both platforms)

1. **FFmpeg** on `PATH` — `ffmpeg -version` must work.
2. **Tesseract OCR** on `PATH` — `tesseract --version` must work.
3. A Python 3.11+ virtual environment with the app installed:

   ```
   python -m venv .venv
   .venv/bin/pip install -e .          # Windows: .venv\Scripts\pip install -e .
   ```

4. A `config.toml` (start from `config.example.toml`, or run `bhqc init`).

Verify all of it in one shot before setting up the service:

```
bhqc -c config.toml doctor
```

---

## Windows

### Option A — NSSM (recommended)

NSSM runs the app as a real Windows service, so it starts at **boot** without
anyone logging in, and restarts automatically if it crashes.

1. Download NSSM (<https://nssm.cc>) and unzip it, e.g. to `C:\nssm`.
2. Edit `service\run-qc.bat` so `INSTALL_DIR` points at the install.
3. From an **Administrator** command prompt:

   ```
   C:\nssm\nssm.exe install BurninghouseQC "C:\BurninghouseQC\service\run-qc.bat"
   C:\nssm\nssm.exe set BurninghouseQC AppDirectory "C:\BurninghouseQC"
   C:\nssm\nssm.exe set BurninghouseQC Start SERVICE_AUTO_START
   C:\nssm\nssm.exe set BurninghouseQC AppStdout "C:\BurninghouseQC\qc_root\service.out.log"
   C:\nssm\nssm.exe set BurninghouseQC AppStderr "C:\BurninghouseQC\qc_root\service.err.log"
   C:\nssm\nssm.exe start BurninghouseQC
   ```

4. Confirm it is running: `bhqc -c config.toml status`

**If the watch folder is a network share**, the service must run as a user that
can see it — the default `LocalSystem` account usually cannot. Set the account
with `nssm set BurninghouseQC ObjectName <DOMAIN\user> <password>`, or map the
share to a drive letter for that account.

### Option B — Task Scheduler (no extra software)

1. Task Scheduler → **Create Task** (not "Basic Task").
2. **General**: "Run whether user is logged on or not", "Run with highest
   privileges".
3. **Triggers**: New → *At startup*. Tick "Repeat task every 5 minutes" with
   "Stop if runs longer than" left blank — this is a cheap restart-on-crash,
   as a second copy exits immediately if the folder is already being watched.
4. **Actions**: Start a program → `C:\BurninghouseQC\service\run-qc.bat`.
5. **Settings**: untick "Stop the task if it runs longer than".

Task Scheduler gives you no log of its own — use `qc_root/burninghouse-qc.log`.

---

## macOS

Use the bundled launchd agent:

```
cp service/com.burninghouse.qc.plist ~/Library/LaunchAgents/
# edit the three paths inside it first
launchctl load -w ~/Library/LaunchAgents/com.burninghouse.qc.plist
```

Check it: `launchctl list | grep burninghouse`, then `bhqc status`.

Two things that bite on macOS:

- **PATH** — launchd does not inherit a shell PATH, which is why the plist sets
  one explicitly. If `bhqc doctor` passes in Terminal but the service reports
  FFmpeg missing, that PATH is why.
- **Full Disk Access** — if the watch folder is on an external volume or in
  `~/Movies`, grant Full Disk Access to `/usr/bin/python3` (or the venv python)
  under System Settings → Privacy & Security.

A LaunchAgent starts at **login**. If the machine must QC without anyone
logging in, move the plist to `/Library/LaunchDaemons/` and load it with
`sudo launchctl load -w`.

---

## Checking on it

| What you want | How |
| --- | --- |
| Is it alive and what's it doing? | `bhqc -c config.toml status` |
| What has it done today? | `qc_root/burninghouse-qc.log` |
| Why did *this* file get routed there? | the `.qc.html` next to the file |
| Are the dependencies still fine? | `bhqc -c config.toml doctor` |

`status.json` is rewritten atomically on every state change, so it is safe to
read at any time — including from another machine over a share, which is the
simplest way to keep an eye on the edit box without sitting at it.

## Restarting after a config change

Thresholds are read when a job starts, but paths are read at start-up, so:

- changed a **threshold** → takes effect on the next file
- changed a **path** → restart the service
- changed the **custom dictionary** → takes effect on the next file, no restart
