# Novashare review, 2026-11-23: what was fixed and how

Two read-only reviews ran on 11-23: `frontend.md` (frontend-design, ui-ux-pro-max) and `backend.md`
(backend-design audit/design/security-discipline). Jason approved fixing every high and medium finding and
swapping each fix live without asking per fix. He also said yes to two choices: admin links for him only,
and a Jellyfin grant for videos.

Each fix works the same way. A patcher edits a fresh copy of the live `~/nuc-homelab/intake`. Its test runs
inside the patched image. Then the live files are backed up to `~/archive/2026-11-23/<tag>/` and the image
is tagged `:before-<tag>`. Finally the new files go in, the container is rebuilt, and the live regression
(`~/sandbox/intake_regress_live.sh`) must print `REGRESSION: ALL PASS`. Steps 3 and 4 are run by
`SWAP-PATCH.sh TAG PATCHER TEST [docker args]`.

| Tag | Commit (nuc-homelab) | Findings | Patcher / test |
|---|---|---|---|
| locks | 5f8cf8c | backend #2: hourly rescan deleted live finish locks | patch_locks.py / test_locks.py |
| srvA | a88913b | backend #1 #3 #4 #7 #9 #11 #13 #14 | patch_srvA.py / test_srvA.py |
| srvB | 253c92f | backend #5 #6 #10 #12 | patch_srvB.py / test_srvB.py |
| ui | f408f10 | frontend #1-#15 (all) | patch_ui.py / test_ui.py (needs a horizon drop.toml) |
| srvC | 85d37d8 | backend #15 #16 #17 (to-do T0970-T0972) | patch_srvC.py / test_srvC.py |

Rollback for one tag: `docker tag nuc-homelab-intake:before-<tag> nuc-homelab-intake:latest && docker compose
-f ~/nuc-homelab/docker-compose.yml up -d intake`, or copy the files back from the archive and rebuild.

## Still open

- **backend #8 (web-to-PDF reaching the LAN by DNS name):** needs a root firewall rule (DOCKER-USER) for the
  `webprint` network. It is in Jason's sudo batch.
- **backend #6, second half:** nothing to do. The drop site in `web/Caddyfile.nuc` has had
  `request_body { max_size 11000MB }` since 11-19, which also covers chunked bodies.
- Everything else in both reports is fixed and live: 31 of 32 findings. #8 is the one left.

## Behaviour changes worth knowing

- `INTAKE_TOKEN` is now required. An empty token stops the container from starting, as `INTAKE_KEY` already did.
- Files over 95 MB that are not videos are held in `.quarantine` as `too-big` (422). The hourly rescan leaves
  them alone, so Jason decides what happens to them.
- Videos need `full` on the auth app `jellyfin` (`INTAKE_VIDEO_GRANT`). On 11-23, only one of Jason's two
  addresses had it. Uploads from anyone else wait in `PENDING_ROOT/<person>/video`.
- The unsorted note is now `<full file name>.intake.json` (it was `<stem>.intake.json`). Notes written
  before 11-23 keep their old names.
- A finish answers 409 "working" for as long as the first finish runs, with no 1 h takeover.
