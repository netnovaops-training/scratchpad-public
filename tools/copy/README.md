# Verified copies (TeraCopy + robocopy + rclone)

Made 2026-11-14. Two kinds of copying:

- **By hand:** right-click files → *Show more options* → **Send to** → **TC Copy to ...** or **TC Move to ...**.
  TeraCopy does the copy and then checks every file against the original (its "verify after copy" is
  already on, using the xxHash3 hash). Destinations: NAS Pictures, NAS photo, NAS Computer backup,
  NAS Music backup, NAS Website backup, and `D:\Inbox`.
  Prefer **Copy**. Move deletes the originals, so only use it once you trust it.
- **Automatic, every night:** `Backup-ToNas.ps1` copies the folders listed in `backup-jobs.json` to the
  NAS and then checks each copied file by hash. Sundays it checks every file and fixes any that differ.
  Nothing on the NAS is ever deleted (unless a job says `"mirror": true`).

## Files

| File | What it does |
|---|---|
| `Copy-Verified.ps1` | One copy job: robocopy (multi-threaded, big files with unbuffered I/O) then a hash check with rclone. Exit 0 = good, 1 = a file differs, 2 = copy errors. |
| `Backup-ToNas.ps1` + `backup-jobs.json` | The nightly job list. Edit the JSON to add, pause (`"enabled": false`) or change jobs. |
| `Register-CopyTasks.ps1` | Creates the 3:15 AM scheduled task. You run it once. |
| `SendTo-TeraCopy.ps1`, `Install-SendTo.ps1` | The right-click Send to entries (already added; `-Remove` takes them out). |
| `Set-TeraCopyReports.ps1` | Makes TeraCopy save a report when a copy has errors (run with TeraCopy closed). |

Logs: `D:\joblog\copy\<time>-<job>\` (one folder per run) and one line per run in `D:\joblog\events.jsonl`.

## One-time setup (you, at the PC)

1. Save the NAS login in Windows (you type the password; it is never written to a file):
   `cmdkey /add:192.168.1.68 /user:<your NAS user name> /pass`
2. Try one job by hand: `pwsh -File D:\scratchpad\tools\copy\Backup-ToNas.ps1 -Only www`
3. Admin PowerShell: `pwsh -File D:\scratchpad\tools\copy\Register-CopyTasks.ps1`
   (asks for your Windows password so it can run while you're signed out; add `-OnlyWhenLoggedOn` to skip that).

## Tested 2026-11-14 (local drives only)

D: → G: with 204 files incl. a 1.1 GB file, spaces, a non-English name, a junction and a `node_modules`
folder: 203 copied (the excluded one skipped, the junction not followed), 203 hash-checked, 0 bad.
Re-run copied nothing. A byte changed on the copy (same size and date) was missed by robocopy but
caught by `-Verify All`, and `-Repair` fixed it. `-Mirror` removed an extra file only at the
destination; `-WhatIf` changed nothing. Works in PowerShell 7 and 5.1.

**Not tested yet:** the NAS (this session can't see it), the Send to entries actually starting TeraCopy,
TeraCopy with a non-English file name in the list, and the scheduled task.
