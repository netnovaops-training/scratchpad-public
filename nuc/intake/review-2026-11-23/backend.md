# Novashare backend review (2026-11-23)

Read-only review of `D:\scratchpad\nuc\intake\` (app.py, resumable.py, share.py, classify.py, dropconfig.py,
scan.py, rescan_held.py, portal.py, themes.py; gotenberg_web_patch.py read for the SSRF question).
Skills applied: backend-design:design (6-step check), backend-design:audit, backend-design:security-discipline.
No code was changed, nothing was run against a live server.

## Summary

1. The front door is solid: known peers only, a shared key checked in constant time, writes need "full", upload ids are bound to the owner, and file names cannot leave their folder.
2. The weak spots are in the filing step and in the "nothing is ever lost" promise. Two files with the same name filed at the same moment can overwrite each other. A NAS that is not mounted catches files on the local disk. /upload deletes the bytes when anything fails.
3. Two smaller gaps: files over 95 MB skip the virus check whatever their type, and the size limit is checked only after the whole body is already on disk (/share has no limit at all).

## Findings

| # | severity | file:line | what is wrong (one concrete failure) | fix in one line |
|---|---|---|---|---|
| 1 | high | app.py:301-312 | `place()` checks `target.exists()` and then uses the fixed name `<name>.part`. A phone sends 3 files at once. Two scans both called `scan.pdf` skip the model (no text layer) and reach `/srv/consume` together. Both see no target and both move into `scan.pdf.part`. The second move overwrites the first file's bytes. The first rename files the second file, and the second rename fails with FileNotFoundError. One file is gone and nothing is kept, because its bytes were overwritten. | Use a unique part name (`.<name>.<random>.part`), then `os.link(part, target)`, which fails if the target exists, and retry with the digest suffix; unlink the part afterwards. |
| 2 | high | resumable.py:117 + app.py:632 + rescan_held.py:35 | `_clear_finish_locks()` runs when `resumable` is imported ("one uvicorn process, so no finish can be running"). The hourly `docker exec intake python rescan_held.py` imports `app`, which imports `resumable`, so at :23 it deletes the live server's `.finishing` locks. A finish still running then (a big file, ClamAV, the model) loses its lock. The phone's retry starts a second filing of the same `.work` folder. One of the two fails and `keep_failed` moves the work folder, including zip members the first finish still needs. | Clear the locks in the app's lifespan start (`_lifespan`), not at module import. Or have rescan_held import only `scan` and a small helper with `place`/`sha256`, not `app`. |
| 3 | medium | app.py:539-549 | `/upload` has no `keep_failed`: any exception in `route_file` (NAS share gone, a disk error, the classify bug in #11) reaches `finally: rmtree(tmp_dir)` and the bytes are deleted. /intake, /share and /up/finish all keep them (T0931 A5). The PC's `/app/file` caller loses the file and gets a 500. | Wrap it the way /intake does: `except Exception: keep_failed(tmp_dir, email, name, [...]); raise` before the `finally`. |
| 4 | medium | app.py:290 (+ app.py:343-345) | `place()` runs `dest_dir.mkdir(parents=True)`. When the NAS share behind `/srv/books/<person>` is not mounted, it creates the folder on the local disk and files the book there. The book disappears behind the mount when the NAS comes back. `INTAKE_SENTINELS` is only checked in `/healthz`, never before filing. | In `place()`, refuse (and keep_failed) when the sentinel for the dest's mount is missing. Use `mkdir(parents=False)` under a known library root. |
| 5 | medium | scan.py:50-51, app.py:413-421 | Any file over `INTAKE_SCAN_MAX_MB` (95) is "skipped" and filed unscanned, whatever its type. The scan.py note assumes "almost always videos". A 96 MB PDF or .docx (padding is easy) goes straight into Paperless or a Kavita shelf with no virus check. | Allow "skipped" only for VIDEO_EXT. For any other type, raise clamd StreamMaxLength/MaxScanSize to cover INTAKE_MAX_MB, or hold the file like "error". |
| 6 | medium | app.py:386-391, 540-545; share.py:368-383 | FastAPI parses the whole multipart body into a spooled temp file BEFORE the handler runs, so the `MAX_MB` check in /intake and /upload only runs after everything has been written. A 50 GB POST fills the TMPDIR disk (the same disk as unsorted and staging) first, and only then gets 413. `/share` has no size check at all, and no free-space reserve check like /up/start has. | Cap the body at the edge (Caddy `request_body max_size`; "check" whether this is already set) and add a `Content-Length` check in the gatekeeper for these 3 paths. Add the RESERVE check to /share. |
| 7 | medium (check) | app.py:105-106, 213-224, 371-372 | While `INTAKE_TOKEN` is unset (allowed for the rollout; only a warning), POST /intake through Caddy with level "full" passes the gatekeeper. /intake then trusts the form field `sender`. Any signed-in person can post `sender=<another person's Telegram id>` and the file is filed as that person, with the caption choosing a shelf. This is impersonation, separate from the known "no write-grant" decision. Check whether the live container has the token set. | Now that n8n sends the token, make an empty `INTAKE_TOKEN` a startup error, like INTAKE_KEY (T0931 item 7). |
| 8 | medium (check) | gotenberg_web_patch.py:20-21; share.py:200-201 | The gotenberg-web deny list is a URL regex, so it blocks IP literals and single-label names but not a public DNS name that resolves to the LAN (for example `192-168-86-86.nip.io` or an attacker's own A record). intake's `_public_url` checks only the top-level URL. A shared public page that embeds `<iframe src="http://nas.attacker.tld:5000/">` makes Chromium fetch a LAN service and print it into the PDF, which is then filed. | Block RFC1918, 100.64/10 and link-local egress for the `webprint` network at the firewall (iptables DOCKER-USER) so name tricks cannot reach the LAN. Check whether such a rule already exists. |
| 9 | medium | resumable.py:498-515 | A finish lock is taken over by AGE (`LOCK_STALE` 1 h), not by whether its owner is still alive. One finish can easily run past an hour: a zip of 200 documents while the PC (the model) is asleep costs up to 2 x 120 s per member through the single model lock, about 13 h. The phone retries, takes over after 1 h, and a second filing runs on the same work folder (same damage as #2). | Single process: keep an in-memory set of uids being finished and answer 409 while the uid is in it. Use the file lock only for restarts (already cleared at startup). |
| 10 | medium | classify.py:82, 257-268 | `_MODEL_LOCK` is a thread semaphore taken INSIDE `run_in_threadpool`. Each file waiting for the model holds a worker thread from the shared pool (anyio default 40) for up to 242 s each while the PC sleeps. Enough waiting files starve every other door, including /healthz's sibling calls and /intake. The comment says the opposite ("keeps the thread pool free"). | Wait for the model with an `asyncio.Semaphore` before entering the thread, or make the model call fail fast (short connect timeout) when the PC is unreachable. |
| 11 | low | classify.py:263, 272 | "NEVER RAISES" does not hold: if the model API answers 200 with `"content": null` (LiteLLM does this for some thinking/tool replies), `re.sub(..., None)` raises TypeError outside the try. The upload errors instead of going to unsorted, and on /upload the bytes are deleted (#3). | `out = (... ["content"]) or ""` inside the try. |
| 12 | low (check) | app.py:576-602 | The "video" hint is never checked against a grant: `GRANT_FOR_KIND` has no "video", so any "full" drop user writes into `/srv/media/general` (Jellyfin). The file's own rule is "a kind with no grant goes to unsorted". Check whether "may use the drop page" is meant to equal "may add to Jellyfin". | Add a `video` library with a grant (for example `jellyfin`) to dropconfig and run it through the same `level_for` block. |
| 13 | low | app.py:431 | The unsorted sidecar is `with_suffix(".intake.json")`, so `bill.pdf` and `bill.docx` share `bill.intake.json`, and the second overwrites the first's note. A duplicate of an already-unsorted file also rewrites the original's sidecar with the new uploader. | Name it `<full file name>.intake.json` and skip writing it on "duplicate". |
| 14 | low | app.py:517, 613-619 | Emails go into intake.log: `level check failed for {email}`, `drop from {email}`, and the "held" `why` (also written to sidecars). T0962 point 5 and T0931 item 14 say the log keeps person ids, not addresses. | Log `who or "unmapped"` and drop the email from the `why` text. |
| 15 | low | resumable.py:455-477 | The old sequential `PUT /up/{uid}?offset=` has no lock. A phone that times out and resends the same offset while the first request is still streaming passes the `offset == have` check twice and appends the piece twice. The file is then bigger than declared, and /finish answers 409 forever. Today's pages always ask for `parallel`, so only an old client or a hand-made call hits this. | Remove the sequential route, or take `flock(LOCK_EX)` on the .part around the check-and-append. |
| 16 | low (check) | app.py:205-227; resumable.py:259 | Writes have no Origin or Sec-Fetch-Site check. Safety rests on the SSO cookie's SameSite setting (in rust/auth, not reviewed). `/up/start` reads JSON without checking Content-Type, so a cross-site `text/plain` fetch counts as a "simple" request. If the cookie is `SameSite=None`, another site could start uploads or POST /share as the visitor. | In the gatekeeper, refuse non-GET requests whose `Sec-Fetch-Site` is `cross-site`. Keep `same-origin` and `none`, so the share sheet still works. |
| 17 | low | resumable.py:65, 298-325 | `fallocate` really reserves blocks. One person may hold `MAX_OPEN` (20) x `INTAKE_MAX_MB` of reserved space for 48 h. Twenty abandoned 10 GB starts take the disk down to the reserve, and everyone else gets 507. | Cap the total declared bytes of a person's open uploads (sum the sizes in `_open_count`). |

## Comment on the approved /intake grant check

Put the grant block from `route_file` (app.py:602-616) into one helper, for example `_hold_unless_granted(verdict, who, email)`,
and call it from both `route_file` and `_intake_sync`. Two details matter for /intake:
- `level_for` needs an email, and /intake only has a person id. Reverse `dropconfig.WHO` to get that person's
  address (fail closed if there is none, so the file is held).
- Check the SENDER's grant, not the person named in the caption. Otherwise a caption ("z") lets one Telegram user
  file into a library on another person's behalf. Finding #7 (the token) should land first, so `sender` can be trusted.

## Design check (6 steps, short)

- Context: HTTP endpoints (four doors), plus an hourly sweep (in-process) and an hourly rescan (cron, separate process).
- Data: files on disk; state lives in `.json`, `.got`, `.finishing`, `.result.json`, `WHY.txt`. The invariant "nothing is lost"
  is enforced by convention in each door, not in one place (that is why #3 slipped).
- Failures: well covered for scanner, model and disk-full. Gaps: concurrent same-name filing (#1), mount loss (#4), and the second process (#2).
- Authorization: the peer, key and level gate plus `level_for` per kind is clear. The gaps are the video kind (#12) and /intake (known, plus #7).
- Idempotence: start, finish and pieces are idempotent. Lock ownership is by age and by file, which a second process can break (#2, #9).
- Observability: one plain log line per action and a real healthcheck. No request id ties the start/pieces/finish lines of one upload
  to its filing line apart from the uid, which /upload and /share do not have (fine for a home server).

## Solid already

- Gatekeeper fails closed: the peer allowlist defaults to 127.0.0.1, INTAKE_KEY missing stops startup, `hmac.compare_digest`, and writes need "full" (allowlist, not denylist).
- Upload ids are 128 random bits bound to the uploader's email, and someone else's id answers 404 (no existence leak).
- Paths: `safe_name` (separators, control and line-separator characters), the `_staged` regex plus the normpath wall, and `_in_tmp`. Staging never uses phone names as paths.
- Resumable: each piece is buffered and SHA-256 checked before a single `pwrite`, a shared/exclusive flock sits between pieces and finish, start and finish are idempotent with a stored result, and unfiled bytes go to `.failed` with WHY.txt.
- One virus gate for every door. Scanner down means held, never filed unchecked. The rescan has its own flock and never deletes.
- Classifier: closed vocabulary, rules in the system message, untrusted text wrapped and its tags stripped, the person never inferred from content, temperature 0.
- dropconfig fails closed (empty people and libraries), destinations are limited to `/srv/` without `..`, and error messages never echo an email.
- `level_for` fails closed, has a timeout, and logs only the exception type (no URL).
- Zips: member count and total size caps (zipfile enforces the declared size when reading), folder parts dropped, hidden files skipped, comics never unpacked.
- SSRF is layered: `_public_url` (including 100.64/10), the IP-pinned `link_probe`, gotenberg-web on its own network with a deny list, and Paperless's gotenberg kept on file:// only.
- `/healthz` is real: a write probe, the free-space reserve, mount sentinels and a clamd ping.
- Error text for people is plain. Exception text goes only to WHY.txt. HTML output is escaped (`share._page`, `portal.page`).
- `fallocate` space reservation, `_write_atomic` for meta files, and log rotation.
