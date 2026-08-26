# Running Burninghouse QC unattended on macOS

Target machine: **macOS 26.5.2 (25F84)**, the second edit machine.

The QC app is a long-running process (`bhqc watch`) that needs to come back on
its own after a reboot, on a machine nobody is sitting at. On macOS that means
a launchd agent.

---

## 1. Install the dependencies

```bash
# Homebrew, if it isn't already there
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install ffmpeg tesseract python@3.13
```

Check both landed on `PATH`:

```bash
ffmpeg -version | head -1
tesseract --version | head -1
```

## 2. Install the app

Put it somewhere outside your home folder. `/Users/Shared` is the pragmatic
choice — it sidesteps the privacy permissions that make `~/Documents`,
`~/Desktop` and `~/Movies` awkward for a background process (see §5).

```bash
sudo mkdir -p /Users/Shared/BurninghouseQC
sudo chown "$(whoami)" /Users/Shared/BurninghouseQC
cd /Users/Shared/BurninghouseQC

git clone https://github.com/camcameronhales/BurninghouseQC.git .
python3 -m venv .venv
.venv/bin/pip install -e .

.venv/bin/bhqc init          # creates config.toml and the QC folders
.venv/bin/bhqc doctor        # verifies FFmpeg, Tesseract, dictionary, folders
```

Edit `config.toml` so `[paths]` points at wherever renders actually land.

## 3. Install the launchd agent

```bash
cp service/com.burninghouse.qc.plist ~/Library/LaunchAgents/
# edit the paths inside it if you didn't install to /Users/Shared/BurninghouseQC
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.burninghouse.qc.plist
```

Confirm it came up:

```bash
launchctl print gui/$(id -u)/com.burninghouse.qc | head -20
/Users/Shared/BurninghouseQC/.venv/bin/bhqc -c config.toml status
```

| Task | Command |
| --- | --- |
| Stop it | `launchctl bootout gui/$(id -u)/com.burninghouse.qc` |
| Restart after a config change | `launchctl kickstart -k gui/$(id -u)/com.burninghouse.qc` |
| Start it again | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.burninghouse.qc.plist` |

> `launchctl load` / `unload` still appear in most tutorials but have been
> deprecated for years. Use `bootstrap` / `bootout` / `kickstart` on macOS 26.

### Agent or daemon?

The plist above is a **LaunchAgent**: it starts when you log in. On a machine
that reboots to the login window and sits there, QC won't run until someone
logs in.

If it must run without a login, move it to a **LaunchDaemon**:

```bash
sudo cp service/com.burninghouse.qc.plist /Library/LaunchDaemons/
sudo chown root:wheel /Library/LaunchDaemons/com.burninghouse.qc.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.burninghouse.qc.plist
```

A daemon runs as root, which brings its own consequences: it can't see mounted
user volumes, and files it creates are root-owned. If the renders live on a
local disk, the daemon is fine. **If they live on a mounted share, stay with the
agent** — the mount only exists inside the logged-in user's session.

The simplest way to get boot-without-login behaviour with an agent is to turn on
automatic login (System Settings → Users & Groups → Automatically log in as) on
a machine that already sits behind a locked door.

## 4. Don't let the Mac sleep through a job

The app holds a `caffeinate` assertion for the duration of each job, so it will
not fall asleep halfway through QC. It deliberately does **not** hold one while
idle, so the machine can still sleep normally between renders.

That covers sleep *during* a job but not the case where the machine is already
asleep when a render lands — a sleeping Mac isn't watching the folder. If files
arrive overnight, either:

```bash
sudo pmset -a sleep 0 disksleep 0          # never sleep (mains-powered desktop)
```

or leave sleep on and accept that the queue drains when the machine next wakes.
Nothing is lost either way: files sitting in `input/` at start-up are picked up
automatically.

## 5. Privacy permissions (the one that catches everyone)

macOS blocks background processes from reading `~/Desktop`, `~/Documents`,
`~/Downloads`, `~/Movies`, iCloud Drive and external volumes unless the binary
has been granted access.

The symptom is distinctive: `bhqc doctor` passes when you run it in Terminal,
but the service logs "permission denied" or simply never notices any files.

Two ways out, in order of preference:

1. **Keep the QC folders outside the protected locations** — `/Users/Shared/...`
   needs no permission at all. This is why the install above puts them there.
2. **Grant Full Disk Access** to the venv's Python: System Settings → Privacy &
   Security → Full Disk Access → **+** → press <kbd>⌘⇧G</kbd> and paste
   `/Users/Shared/BurninghouseQC/.venv/bin/python3`. Restart the agent
   afterwards with `launchctl kickstart -k`.

Note that granting access to a *symlink* doesn't work — resolve it first with
`readlink -f /Users/Shared/BurninghouseQC/.venv/bin/python3` and add the real
binary.

## 6. If renders land on a NAS or SMB share

Read **[`server-safety.md`](server-safety.md)** first — it covers what the app
does and does not write, and why the default mode leaves the share alone. The
single most useful thing you can do is give the account this service runs as
**read-only** access to the renders share, and full access only to the local QC
folder. Then no bug can write to the server, whatever the config says.


macOS delivers filesystem events through FSEvents, which **does not fire for
SMB or AFP mounts**. A render dropped onto a share would otherwise sit in the
input folder forever.

The app detects this and switches to polling automatically — you'll see this in
the log at start-up:

```
Input folder is on //cam@nas.local/renders — using the polling watcher (every 5s)
```

To force it either way:

```toml
[watcher]
use_polling      = "auto"    # "true" to force polling, "false" to force FSEvents
polling_interval = 5.0
```

Two other things about shares worth knowing:

- Raise `stability_checks` (or `poll_interval`) if writes over the network stall
  long enough to look like a finished render.
- The share must be mounted before the agent runs. If it isn't, add it to
  System Settings → General → Login Items so it mounts at login, and keep
  `KeepAlive` set so the agent retries.

## 7. Checking on it

| What you want | How |
| --- | --- |
| Is it alive and what's it doing? | `bhqc -c config.toml status` |
| What has it done today? | `qc_root/burninghouse-qc.log` |
| Did launchd fail to start it? | `qc_root/launchd.err.log` |
| Why did *this* file get routed there? | the `.qc.html` next to the file |
| Are the dependencies still fine? | `bhqc -c config.toml doctor` |

`status.json` is rewritten atomically on every state change, so it's safe to
read at any moment — including over a share from your main machine, which is
the easiest way to keep an eye on the edit box without walking over to it.

## 8. After a change

- changed a **threshold** → takes effect on the next file, no restart
- changed the **custom dictionary** → next file, no restart
- changed a **path**, or updated the code → `launchctl kickstart -k gui/$(id -u)/com.burninghouse.qc`

---

## Other platforms

Windows was scoped out once the edit machine was confirmed as a Mac. Nothing in
the app is macOS-only — `bhqc watch` runs anywhere Python, FFmpeg and Tesseract
do, and `power.py` no-ops off macOS. Only this service wrapper would need
writing (NSSM or Task Scheduler). The git history has an earlier Windows
launcher if it's ever needed.
