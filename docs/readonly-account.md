# Setting up the read-only QC account

> **Not currently in use.** Both installs run against local storage, so no
> shared-storage account is needed. Kept for the day that changes.
> [`local-trial.md`](local-trial.md) is the setup actually in use.

**Why bother:** the app's default mode never writes to the renders folder. This
makes that a property of the filesystem instead of a promise in a config file.
With a read-only account, no bug, no bad config and no future change to this
code can write to the server. It is the one safeguard that doesn't depend on
the app behaving.

Budget 15 minutes. There are three parts: an account on the server, a mount on
the Mac, and a verification step.

> **Before you start**, find out which of these your server is — the steps
> differ, and everything after §1 is the same:
> Synology · QNAP · Windows Server / Windows share · macOS File Sharing · Linux
> Samba · NFS

---

## 1. Create the account on the server

Call it something obvious like **`qc-readonly`**. Give it a long random
password — nobody types this again after §2.

### Synology (DSM 7)

1. **Control Panel → User & Group → User → Create.**
   Name `qc-readonly`, uncheck "Allow the user to change account password".
2. **Join groups:** `users` only. Not `administrators`.
3. **Assign shared folders permissions:** find the renders share and tick
   **Read only**. Everything else: **No access**.
4. **Assign application permissions:** deny everything except **SMB** (or AFP
   if that's what you use). No DSM login, no File Station, no FTP.
5. **User quota / speed limit:** leave alone.

Then **Control Panel → Shared Folder → [renders share] → Edit → Permissions**
and confirm `qc-readonly` shows **Read only** there too. DSM has two places
that can disagree; the more restrictive wins, but check both so you know what
you have.

### QNAP (QTS)

1. **Control Panel → Privilege → Users → Create a User.**
2. **Edit Shared Folder Permission** for the new user: the renders share gets
   **RO**; everything else **Deny**.
3. **Control Panel → Privilege → Users → [user] → Edit Account Profile →**
   ensure no admin group membership.

### Windows Server / a Windows machine sharing the folder

1. **Computer Management → Local Users and Groups → Users → New User**
   (or an AD account if the server is domain-joined).
2. Right-click the renders folder → **Properties → Sharing → Advanced Sharing →
   Permissions**: add `qc-readonly`, tick **Read**, untick Change and Full
   Control.
3. **Properties → Security** (NTFS, the one that actually decides): add
   `qc-readonly` with **Read & execute**, **List folder contents**, **Read**.
   Nothing else. NTFS and share permissions are ANDed — the stricter wins — so
   set both.

### macOS File Sharing (if the "server" is another Mac)

1. **System Settings → Users & Groups → Add Account** → *Sharing Only* account
   named `qc-readonly`.
2. **System Settings → General → Sharing → File Sharing → (i)**, select the
   renders folder, and set `qc-readonly` to **Read Only**.

### Linux Samba

In `smb.conf`:

```ini
[renders]
   path = /srv/renders
   valid users = @editors qc-readonly
   read list = qc-readonly
   write list = @editors
   browseable = yes
```

`smbcontrol all reload-config` (or restart `smbd`) afterwards.

### NFS

NFS trusts the client's UID rather than an account, so the read-only bit goes
on the export instead. In `/etc/exports`:

```
/srv/renders  <mac-ip>(ro,sync,no_subtree_check)
```

then `exportfs -ra`. Note this makes the share read-only for that *machine*,
not for a user — so the Mac's editor account loses write access too. If that
matters, use SMB for the QC mount instead.

---

## 2. Mount it read-only on the Mac

Two things to get right: the credentials must be stored so no human is present
at boot, and the mount has to exist before the QC service tries to read it.

### Store the password in the keychain

Do this once, logged in as the account the QC service runs as:

1. **Finder → Go → Connect to Server** (<kbd>⌘K</kbd>)
2. `smb://<server>/<renders-share>`
3. Enter `qc-readonly` and its password, and **tick "Remember this password in
   my keychain"**.

That keychain entry is what lets it reconnect unattended.

> Never put the password in `config.toml`, in a script, or anywhere in this
> repo. The keychain is the only place it should live.

### Make the mount read-only at the client too

The server-side permission is the real enforcement; this is a second belt.

```bash
mkdir -p /Users/Shared/BurninghouseQC/mnt/renders
mount_smbfs -o rdonly "//qc-readonly@<server>/<renders-share>" \
  /Users/Shared/BurninghouseQC/mnt/renders
```

With the keychain entry in place this won't prompt.

### Make it come back after a reboot

**System Settings → General → Login Items → Open at Login → +** and add the
mounted share. It reconnects when the QC account logs in.

This is also why the QC service is a **LaunchAgent, not a LaunchDaemon**: a
daemon runs before login and outside the user session, so it cannot see a
user-mounted share. If you need boot-without-login, turn on automatic login for
the QC account rather than switching to a daemon (see `service-setup.md` §3).

---

## 3. Point the config at it and verify

```toml
[paths]
input = "/Users/Shared/BurninghouseQC/mnt/renders"

[routing]
mode = "report_only"
```

Then, **as the account the service runs as**:

```bash
bhqc -c config.toml check-access
```

This doesn't read the config and take its word for it — it tries the
operations. It creates a zero-byte probe file and immediately deletes it in
each folder, which is how a read-only share is confirmed to actually be
read-only. (`--no-write-probe` skips it, at the cost of skipping the check.)

What you want to see:

```
[  ok  ] input folder is readable
         /Users/Shared/BurninghouseQC/mnt/renders — readable

[  ok  ] input folder location
         //qc-readonly@server/renders (network share)

[  ok  ] input folder is read-only
         YES — writes refused (Permission denied)

[  ok  ] pass folder     ... writes are permitted
[  ok  ] review folder   ... writes are permitted
[  ok  ] error folder    ... writes are permitted
[  ok  ] work folder     ... writes are permitted

[  ok  ] routing mode
         report_only — renders are never touched

All good — the account has read-only access to the renders and full
access to its own folders.
```

It exits `0` when usable and `1` when something must be fixed, so it drops
straight into a setup script.

### What the results mean

| Result | Meaning |
| --- | --- |
| `input folder is read-only: YES` | The guarantee is real. Done. |
| `input folder is read-only: NO` (warn) | Still usable — nothing writes there in `report_only` — but the account can write, so it's a promise, not a guarantee. Recheck §1. |
| `input folder is readable: FAIL` | Almost always the share isn't mounted, or the QC account isn't the one that mounted it. |
| `routing mode: move` + read-only input | Caught deliberately: every file would fail to move. Set `mode = "report_only"`. |
| a QC folder `FAIL` | The account needs full access to its *own* folders. Check ownership of `/Users/Shared/BurninghouseQC`. |

Finally, confirm QC actually runs under the restricted account:

```bash
bhqc -c config.toml run "/Users/Shared/BurninghouseQC/mnt/renders/some_master.mov"
```

You should get a verdict, a report in the QC folder, and:

```
Source left untouched: /Users/Shared/BurninghouseQC/mnt/renders/some_master.mov
```

---

## Troubleshooting

**"Operation not permitted" reading the share, but Finder can see it.**
The service is running as a different user than the one holding the mount, or
it's a LaunchDaemon. Check with `launchctl print gui/$(id -u)/com.burninghouse.qc`.

**The mount disappears after a while.**
macOS drops idle SMB mounts. `KeepAlive` restarts the service, but the *mount*
needs to come back too — the Login Item handles a reboot; for mid-day drops,
`check-access` failing loudly in the log is your signal. If it becomes a
nuisance, an autofs entry in `/etc/auto_smb` remounts on demand.

**Renders are never noticed even though the folder is readable.**
FSEvents doesn't fire on SMB. The app detects this and polls instead — look for
`using the polling watcher` in the log at start-up. Force it with
`watcher.use_polling = "true"` if the detection missed.

**`check-access` says read-only, but a file still got modified.**
That shouldn't be possible, and it isn't something to shrug at. Grab the log,
the `.qc.json` sidecar for the file, and open an issue — but check first that
nothing else (an NLE, a sync client, a backup agent) has the folder open.

---

## What this does and doesn't protect against

**Does:** any write from this app to the renders share — a bug, a
misconfiguration, a future change, or `mode = "move"` set by mistake. All of
them fail at the filesystem instead of doing damage.

**Doesn't:** anything else on the machine. This account is for the QC service;
it isn't a substitute for backups, and it doesn't protect the share from the
editor accounts that legitimately write to it. It closes exactly one hole, and
closes it properly.
