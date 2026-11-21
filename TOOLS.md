# Scratchpad tools

One line per script, taken from each script's own header. Regenerate with `python tools/make_tools_index.py`.

## nuc

- `break_finder_daily.py`: run the break-finder every day and flag only paths that NEWLY went missing (T0004 step 5).
- `caddy_pi_tools_patch.py`: SSO subdomains for the Pi's sandbox browser and qBittorrent (T0872, 2026-11-18).
- `github_merge.py`: merge the SAFE Dependabot pull requests on GitHub at noon and midnight New York time.
- `github_pull.py`: copy GitHub's own code review, lint and analysis results to the NUC, twice a day (2026-11-17).
- `github_stable.py`: keep a 'stable' branch (last known-good) next to 'main' (beta) in every GitHub repo.
- `nuc_alert_mail.py`: email Jason the notes that root jobs leave in ~/swap-care.alert, then remove them (2026-11-18).
- `runtime_bumps_1am.sh`: one-off for 2026-11-20 01:00 New York (06:00 UTC): test the two Python 3.15
- `weekly_updates.py`: Tuesday 10 AM (Eastern) email: ONLY the containers that have a newer image (2026-11-18).

## nuc/backlog

- `t0970_prepare.py`: T0970 step 1 (autopilot 2026-11-17): prepare ~/backlog-page.new for the swap.

## nuc/bin

- `backup-meta.sh`: copy the NUC's out-of-home config into ~/.backup-meta so the
- `config_export.py`: write a secret-free copy of the NUC's and Pi's config into ~/config-export.
- `kopia-nuc.sh`: back up the NUC's home folder into the SAME Kopia repository KopiaUI uses on the PC
- `kopia-vault.sh`: second, local backup of the NUC's home folder onto the NUC's own USB SSD (/mnt/vault, 1 TB,
- `restore-drill.sh`: monthly proof that the NUC backups can actually be restored (T0004 step 4).
- `sqlite-dump.sh`: nightly CONSISTENT copies of every live SQLite database on the NUC (T0010, 2026-11-18).
- `wv-dump.sh`: nightly consistent dumps of the two weather-validation MariaDB databases (T0009, 2026-11-18).

## nuc/intake

- `app.py`: The intake door. n8n hands it a file; it decides and files it.
- `atomic_patch.py`: wire resumable.py + atomic_ui.py into intake (T0884, 2026-11-18). Run inside a COPY of
- `atomic_ui.py`: Atomic Share on the phone: the service worker and the "sending" page (T0884 step 1, 2026-11-18).
- `gotenberg_web_patch.py`: add a separate Gotenberg for printing shared links to PDF (T0872, 2026-11-18).
- `portal.py`: The drop page. Who you are decides where it goes.
- `resumable.py`: Resumable uploads for Atomic Share: big files survive a dropped signal (T0884 step 1, 2026-11-18).
- `share.py`: Phone "Share" -> Drop. Makes the drop page an installable app that shows up in the phone's share sheet.
- `share_patch.py`: wire share.py into the intake service (T0872, 2026-11-18). Run in the intake folder.
- `share_test.sh`: sandbox test of the /share door (T0872). Runs in a curl container on nuc-homelab_default
- `test_resumable.py`: sandbox test for resumable.py (T0884, 2026-11-18). Talks to the SANDBOX intake

## nuc/root

- `root-sbin-export.sh`: root-sbin-export - root's nightly copy of the root-only /usr/local/sbin/vault-* scripts into a ROOT-OWNED
- `test_root_sbin_export.sh`: run root-sbin-export.sh as root inside a throwaway container with fake

## nuc/weather-validation

- `common.py`: Shared pieces: config, database access, small helpers.
- `discover.py`: Find GHCN-Daily stations near home (NOAA NCEI search service) and print them as config snippets.
- `export_parquet.py`: Nightly export: weather_reference, forecast_log and the garden weather table -> monthly Parquet files.
- `forecast_logger.py`: Log forecasts into forecast_log: NWS + Open-Meteo models (+ Jason's model by import). Idempotent upserts.
- `forecast_sources.py`: Forecast sources -> rows for forecast_log.forecast (SI units, UTC).
- `http_client.py`: Polite HTTP client: spacing between requests, retries with backoff, JSON only.
- `ref_sources.py`: Reference observation sources -> rows for weather_reference (SI units, UTC).
- `reference_loader.py`: Load weather_reference: bounded, incremental, resumable, idempotent.
- `schema.py`: Table definitions the code relies on (a test compares them with the real databases).
- `score_forecasts.py`: Match forecasts to what happened, then write per-day and per-week accuracy tables to JSON.
- `validation.py`: weather-validation: the comparison side (NOT part of Robin's site).
- `wv.sh`: wv.sh  build | unit | test | init-secrets | up | down | status | run <command...> | logs [service]

## pc/ai-stack

- `ai-compose.cmd`: `docker compose` for this stack, with secrets filled in from Proton Pass.
- `apps.cmd`: Update Windows apps with winget. NOT the Docker stack - that is update.cmd.
- `apps.ps1`: (no header comment yet)
- `auth-setup.ps1`: =============================================================================
- `autostart.cmd`: What runs automatically, and what still needs you.
- `autostart.ps1`: (no header comment yet)
- `certs.cmd`: What certificate each site is actually serving, right now.
- `certs.ps1`: (no header comment yet)
- `certwatch.cmd`: Check the certificates all three sites are serving, and warn before one expires.
- `certwatch.ps1`: (no header comment yet)
- `db-export.ps1`: Export every database on SuperNova. Mondays and Thursdays, 07:00.
- `diag.cmd`: Collect everything needed to diagnose the web stack into diagnostics.txt
- `diag.ps1`: (no header comment yet)
- `docker-fix.ps1`: - what the `fix` command runs (Jason, 2026-11-04: "type fix over SSH and it
- `env-sync.cmd`: Add new settings to .env without touching what you've already filled in.
- `env-sync.ps1`: (no header comment yet)
- `export.cmd`: Export every transcript to markdown.
- `files-backup.sh`: Back everyone's files up to the encrypted external SSD on the NUC.
- `firewall.cmd`: Open the Windows firewall for ports 80 and 443 - the web server, and
- `firewall.ps1`: (no header comment yet)
- `fix-run.ps1`: - what typing `fix` does. Starts the "Docker Fix" scheduled task (install-fix.ps1
- `fix.cmd`: fix -- restart Docker on SuperNova from anywhere (SSH or a local prompt). See fix-run.ps1.
- `gpu-check.cmd`: Why can't a container see the 4070?
- `gpu-check.ps1`: (no header comment yet)
- `hermes-launch.ps1`: (no header comment yet)
- `hermes.cmd`: Open the Hermes CLI agent. Double-click this, or use the Desktop shortcut.
- `hostmetrics.cmd`: The metrics only Windows can see: C: free space, CPU temperature, Windows
- `hostmetrics.ps1`: (no header comment yet)
- `index-feed.ps1`: Ship the PC's searchable data to the NUC's index. Hourly.
- `install-fix.ps1`: (no header comment yet)
- `install-shortcut.cmd`: Put a Hermes shortcut with its icon on your Desktop and in the Start menu.
- `install-shortcut.ps1`: (no header comment yet)
- `logs.cmd`: Tail the logs of one container.
- `models.cmd`: Download or refresh the models listed in .env (PULL_MODELS).
- `n8n-check.cmd`: Find out why n8n is losing its login and workflows.
- `n8n-check.ps1`: (no header comment yet)
- `n8n-export.cmd`: Pull the workflows out of n8n and back into n8n\workflows.
- `n8n-export.ps1`: =============================================================================
- `people-mirror.ps1`: keeps the PC's copy of the sign-in account list the same as the NUC's.
- `places.cmd`: Add, remove and list places without touching the curated file.
- `preflight.cmd`: Run this BEFORE setup.cmd.
- `preflight.ps1`: (no header comment yet)
- `printserver.cmd`: The print server. Prints what you approve from Telegram, and tells you
- `printserver.ps1`: (no header comment yet)
- `restart-docker.ps1`: - unfreeze Docker Desktop and keep the GPU free.
- `routes.cmd`: Which model answers which job - proved, not assumed.
- `routes.ps1`: (no header comment yet)
- `run-backup.cmd`: Nightly household backup to the encrypted external drive on the NUC.
- `rust-setup.ps1`: (no header comment yet)
- `rust.cmd`: Install Rust on this PC, so you can edit the sites with a two-second
- `setup.cmd`: One-time setup. Just double-click this file.
- `setup.ps1`: (no header comment yet)
- `start-all.cmd`: Start EVERYTHING - the AI stack, the phone line, and the websites.
- `start.cmd`: Start the AI stack - models, router, Hermes, Telegram.
- `stop.cmd`: Stop everything. Your data, models and certificates are kept.
- `update.cmd`: Apply changes to a stack that's already running.
- `update.ps1`: (no header comment yet)
- `vector.ps1`: =============================================================================
- `verify.cmd`: Check that everything actually works.
- `verify.ps1`: (no header comment yet)
- `voices.cmd`: What voices you have, which surface uses which, and what they sound like.
- `voices.ps1`: (no header comment yet)
- `websites.cmd`: Bring the websites up, staged so a misconfiguration can't burn your
- `websites.ps1`: (no header comment yet)

## pc/ai-stack/colibri

- `Get-Model.ps1`: (no header comment yet)
- `Watch-Model.ps1`: (no header comment yet)
- `install-resume-startup.ps1`: (no header comment yet)
- `install-resume-task.ps1`: (no header comment yet)
- `pull-dsv41.ps1`: (no header comment yet)
- `resume-dsv41.ps1`: (no header comment yet)
- `resume-once.ps1`: (no header comment yet)

## pc/ai-stack/collage

- `app.py`: The collage: photographs of a wall, in the order they hang on it.
- `fixed_corner.py`: Clean the Gemini sparkle where the DETECTOR cannot see it, using position.
- `unwatermark.py`: Take the Gemini sparkle off a whole folder, in place.
- `watermark.py`: Find Gemini's sparkle and paint it out.

## pc/ai-stack/dj

- `app.py`: The DJ book: nights worked, and the photographs from them.

## pc/ai-stack/files/app

- `__init__.py`: (no header comment yet)
- `main.py`: /files — everyone sees what is theirs, and can take it away.

## pc/ai-stack/hermes

- `load_github_facts.py`: Load GitHub facts into one person's Hermes memory, through Hermes' own MemoryStore.

## pc/ai-stack/hermes/app

- `__init__.py`: (no header comment yet)
- `agent.py`: The Hermes agent loop: model -> tool calls -> results -> model, until it
- `api_tools.py`: Free plant and web-search API tools for Hermes (all READ-ONLY).
- `cli.py`: Hermes CLI - the terminal front end.
- `doc_tools.py`: Knowledge-store tools for Hermes: search the documents saved in Paperless.
- `house.py`: The house tools -- Hermes stops being a stranger in its own home.
- `memory_tools.py`: The memory tools, bound to ONE person's store.
- `plant_tools.py`: Plant lookup tools for Hermes.
- `server.py`: Hermes over HTTP, so a phone can talk to the same assistant the CLI does.
- `speak.py`: Say the same thing the way each person actually reads.
- `threads.py`: The conversation each person left off in, kept on the server.
- `tools.py`: Hermes tool belt.
- `ui.py`: How Hermes looks in the terminal.

## pc/ai-stack/litellm

- `model-bench.py`: Can gemma4:12b replace five models on this card?

## pc/ai-stack/metrics

- `app.py`: Record the machine into SQLite every two minutes. Keep one month. Hand it

## pc/ai-stack/peewee-tools

- `build.py`: =============================================================================
- `collage_replace.py`: Rebuild the drifting mosaic behind Pee-Wee's name.
- `connections.py`: =============================================================================
- `handoffs.py`: =============================================================================
- `intro_fx.py`: The cold open: an atom, then the supernova, then the wave going out.
- `intros.py`: =============================================================================
- `make_insignia.py`: Render the insignia on its own, for any project.
- `make_video.py`: The Pee-Wee story, exactly as the site tells it, as one MP4.
- `mapdata.py`: =============================================================================
- `mapfilm.py`: The map act: Albany under a camera that flies where the story goes.
- `mapregion.py`: =============================================================================
- `narrate.py`: =============================================================================
- `patter.py`: =============================================================================
- `portrait_act.py`: The page's own opening: his face, and the three lines under it.
- `resize_all.py`: =============================================================================
- `showcase.py`: =============================================================================
- `songscrape.py`: =============================================================================
- `suggest.py`: =============================================================================
- `wall_replace.py`: Put a folder of photographs on Pee-Wee's wall, replacing what is there.

## pc/ai-stack/peewee-tools/studio

- `app.py`: =============================================================================
- `run-studio.cmd`: Pee-Wee Studio -- double-click me.

## pc/ai-stack/pi

- `kuma-add-hosts.py`: Add plain ping monitors for hosts. Run ON THE PI, as root, container STOPPED.
- `kuma-add-power.py`: Add PUSH monitors that warn about under-voltage. Run ON THE PI, kuma STOPPED.
- `kuma-add-services.py`: Monitor the services that went down unnoticed in the 2026-09-13 outage.
- `kuma-fix-addresses-and-power.py`: Fix the NUC Kuma: correct the Pi 3's address, and move power alerting here.
- `kuma-notify.py`: Attach the ntfy channel to every Kuma monitor. Run ON THE PI, as root,
- `kuma-nuc-hosts.py`: Add host monitors to the NUC's Kuma. Run ON THE NUC, container STOPPED.
- `kuma-seed.py`: Seed the Pi's Uptime Kuma with the checks that would have caught tonight.
- `ntfy-users.sh`: Create the ntfy accounts. Run ON THE PI.
- `offload-videos.sh`: Move finished downloads off the Pi and onto the NAS, and only then delete.
- `pi-power-watch-install.sh`: Install the power watcher and its timer. Run ON A PI.
- `pi-power-watch.sh`: Report this board's power health to Kuma. Run by a systemd timer.
- `pi-sdcard-guard.sh`: Cut what the Pi writes to its SD card, so an always-on node does not eat it.
- `pi-wipe.sh`: Wipe the Pi back to a clean compute/monitoring node -- without bricking it.
- `pi3-bootstrap.sh`: Pi 3 "Tor" -- the one step that needs your password, so nothing else does.
- `pi3-lowpower.sh`: Cut the Pi 3's current draw so a marginal supply stops browning out.
- `pi3-tor.sh`: Tor on the Pi 3, as a CLIENT + LAN SOCKS proxy. Never a relay, never an exit.
- `pi3-usb-mount.sh`: Mount the USB stick NON-DESTRUCTIVELY and give the Pi a scratch area on it.
- `pi5-ssd-downloads.sh`: Move the Pi 5's torrent storage from sshfs-over-the-network to a local SSD.
- `playlist-match.py`: Turn a Spotify or YouTube playlist into a Navidrome playlist of YOUR files.
- `stack-ensure-install.sh`: Install stack-ensure as a boot-and-every-10-minutes systemd timer.
- `stack-ensure.sh`: Bring up anything in the Pi 5 stack that is not running.
- `tang-bind.sh`: Bind the NUC's LUKS vault to the Tang server on the Pi. RUN ON THE NUC.
- `usb-disk-setup.sh`: Format and mount a USB SSD for the download lane. Run ON THE PI.
- `vpn-killswitch-test.sh`: PROVE the kill switch. Not "the firewall rules look right" -- actually

## pc/ai-stack/pi-digits

- `app.py`: /pi -- compute pi on SuperNova and keep only the equation and its answers (2026-11-08 / 11-09).
- `snpi.py`: - pi.snpi version 2: the stored result is an EQUATION plus its answers, nothing else
- `verify_pi.py`: - check SuperNova's pi.snpi yourself. Nothing here trusts SuperNova.

## pc/ai-stack/pi-digits/ycruncher

- `install-task.ps1`: - RUN ONCE AS ADMINISTRATOR. Registers the scheduled task "Pi y-cruncher" that runs
- `ycrun.ps1`: - runs y-cruncher ELEVATED for /pi (Jason, 2026-11-08: "use both C and D and other drives ... go as many digits as possible").

## pc/ai-stack/printq

- `app.py`: The print queue watcher, talking to CUPS instead of Windows.

## pc/ai-stack/scratchpad/files

- `add_ps_comments.py`: Insert plain-English comments above PowerShell functions that have none (comment-only).
- `append_tsv.py`: Append new translations to lang/ko.tsv without disturbing existing lines.
- `clean7.py`: (no header comment yet)
- `cmp_md5.py`: (no header comment yet)
- `find_download.sh`: (no header comment yet)
- `fixsc.py`: (no header comment yet)
- `g13.py`: (no header comment yet)
- `g14.py`: (no header comment yet)
- `g19.py`: (no header comment yet)
- `gch2.py`: (no header comment yet)
- `gch3.py`: (no header comment yet)
- `gmail.py`: (no header comment yet)
- `grm.py`: (no header comment yet)
- `h2t.py`: (no header comment yet)
- `helpers.sh`: ---------- language: English, Korean, or both -----------------------------------
- `howsearch.py`: (no header comment yet)
- `html_fix.py`: (no header comment yet)
- `linkcheck.py`: (no header comment yet)
- `menu.sh`: ---------- the menu -------------------------------------------------------------------
- `merge14.py`: (no header comment yet)
- `peek2.py`: (no header comment yet)
- `pii.py`: (no header comment yet)
- `plan_email.py`: (no header comment yet)
- `ps1.py`: (no header comment yet)
- `renum.py`: (no header comment yet)
- `router.py`: (no header comment yet)
- `rows.py`: (no header comment yet)
- `rows2.py`: (no header comment yet)
- `stop-wiki.sh`: Test-only: stop the wiki page import and wait for the install to finish.
- `subnet-check.sh`: Compare the old and the new way of finding the home network.
- `t11.sh`: (no header comment yet)
- `t6.sh`: (no header comment yet)
- `test-pipefail.sh`: Does "long output | grep -q" give a wrong "no" under pipefail? (garden.sh uses set -uo pipefail)
- `to_author.py`: 'the previous owner' -> 'the author' (Korean 이전 주인 -> 제작자, with the right particles).
- `verify.py`: (no header comment yet)
- `wgi.py`: (no header comment yet)
- `wire.py`: (no header comment yet)
- `wsl-prep.sh`: Test-only preparation of the WSL "junstest" distro to act like Robin's laptop.
- `wsl-prep2.sh`: Test-only preparation of the WSL "junstest" distro to act like Robin's laptop.

## pc/ai-stack/scratchpad/files/jgp

- `apply.py`: (no header comment yet)
- `verify.py`: (no header comment yet)

## pc/ai-stack/scratchpad/files/mine

- `add_spec.py`: (no header comment yet)
- `ai_tabs_spec.py`: (no header comment yet)
- `form_spec.py`: (no header comment yet)
- `pipeline_spec.py`: (no header comment yet)
- `ref.py`: (no header comment yet)
- `wl_spec.py`: (no header comment yet)

## pc/ai-stack/scratchpad/files/sp

- `ins.py`: (no header comment yet)

## pc/ai-stack/scratchpad/files/wikitest

- `login_check.py`: (no header comment yet)
- `verify.py`: Test helper: checks the test wiki at http://127.0.0.1:13000 against the seed.

## pc/ai-stack/scripts

- `Update-Stack.ps1`: (no header comment yet)
- `audio_files.py`: keep every website audio file in web/public/Audio-Files, with links at the old
- `backup_to_nas.cmd`: Nightly copy of the PC's work to the NAS (written 2026-10-31).
- `build_code_inventory.py`: Write SCRATCHPAD.md: every code file in ai-stack, what it does, and whether it is used.
- `doc_sidefx.py`: redo ONLY the "Side effects" section of docs that say "none" (2026-11-02).
- `docs_status.py`: where is the documentation backfill? (2026-10-31)
- `drive-sync.sh`: Pull Google Drive down to this box, on a timer.
- `export_transcripts.py`: Export transcripts.
- `import_graph.py`: which Python file imports which other file in this repo (2026-11-01).
- `paperless_load.py`: Load text documents into Paperless so Hermes can search them.
- `paperless_people.py`: Tag every All-Documents file in Paperless with its person, and lock personal ones away from Hermes.
- `places_import.py`: Add places from Google Maps.
- `publish-ipa.sh`: Publish an iOS build from Codemagic, and read its version out of the file.
- `publish_documents.py`: turn All-Documents\Projects\Homelab Docs (was Documents\Homelab Docs) into pages on the site (2026-10-31).
- `register_oidc_client.py`: Register one OIDC client with the household issuer, without printing it.
- `release-app.sh`: Build Cut The Fluff, publish it, and prove what is being served.
- `seed-plant-pages.py`: Build the backlog of Robin's wiki: a page for every plant he grows.
- `seed-wiki-nav.py`: Give Robin's wiki a sidebar, so the pages are findable by browsing.
- `split_todo_note.py`: split the Proton Pass "To Do" note into numbered part notes (2026-10-31).
- `sync_documents.py`: Nightly: new and changed documents in All-Documents go to the NUC and into Paperless.
- `sync_scratchpad.py`: copy this PC's script folders to the NUC for the code scratchpad
- `tidy_watch.py`: keep All-Documents and image-files sorted as Jason drops new files in.
- `wikijs.py`: Talk to Wiki.js over its GraphQL API.

## pc/ai-stack/shared

- `__init__.py`: (no header comment yet)
- `distil.py`: Turn a person's activity log into facts worth keeping.
- `health.py`: One screen that answers "is anything wrong right now".
- `history.py`: Turning a month of samples into something you can read.
- `identity.py`: Who is talking to Hermes, on whichever surface they are talking from.
- `invites.py`: Invite links for the guest bot: one tap to join, nothing to transcribe.
- `memory.py`: Per-person memory for Hermes: markdown files in a directory that person owns.
- `models_health.py`: Which job routes actually work, and which are quietly broken.
- `offers.py`: Ask once, keep it everywhere: the household's shared yes/no queue.
- `printing.py`: The print server's brain: everything that decides, nothing that touches a
- `report.py`: The 8pm report: one message, once a day, that you actually read.
- `router.py`: Thin client for the LiteLLM router.
- `stats.py`: /stats - one command, two depths.
- `transcript.py`: Shared transcript log.
- `usage.py`: GPU and LLM usage, in one place, for both Hermes and the Telegram bot.
- `voice.py`: Client for the cloned-voice TTS service.

## pc/ai-stack/stt

- `server.py`: Local speech-to-text. Whisper on your GPU, two ways in.

## pc/ai-stack/tables

- `app.py`: The console — one tab per thing you run, all in one page.
- `store.py`: Reading and writing the JSON files in Admin\data.
- `targets.py`: What each data file is, and what shape it has.

## pc/ai-stack/telegram

- `bot.py`: Telegram bridge.

## pc/ai-stack/voice

- `narrate.py`: Turn a document into an audiobook in one of the house's cloned voices.
- `narrate_worker.py`: Pick up queued documents and read them aloud into the audiobook shelf.
- `server.py`: The voice. One service, three consumers, one copy of the weights in VRAM.
- `transcribe-references.py`: Write an accurate transcript beside every reference clip.
- `trim-references.py`: Cut every voice reference down to the window the models actually want.

## pc/ai-stack/watchdog

- `watch.py`: Watch the other machine, and say so on Telegram when it stops answering.

## pc/apps/CleanMyPhone

- `gradlew.bat`: (no header comment yet)

## pc/apps/CutTheFluff

- `gradlew.bat`: (no header comment yet)

## pc/apps/Garden

- `gradlew.bat`: (no header comment yet)

## pc/apps/GardenApp

- `gradlew.bat`: (no header comment yet)

## pc/archive-tools

- `archive_by_inventory.py`: move what SCRATCHPAD.md marks "outdated" / "one time use" out of ai-stack.
- `safe_unzip.py`: safe_unzip.py ZIP OUTDIR: extract a zip only if no entry escapes OUTDIR (absolute or ../ names).
- `tidy_ai_stack.py`: move backup copies, archives and zips out of the ai-stack repo to D:.

## pc/checks/local

- `check.sh`: The same checks as the shared GitHub workflows, run on this computer.

## pc/desktop/Hermes

- `hermes-launch.ps1`: (no header comment yet)

## pc/desktop/Restart Docker

- `restart-docker.ps1`: - unfreeze Docker Desktop and keep the GPU free.

## pc/desktop/update all apps

- `winget-update.ps1`: (no header comment yet)

## pc/doc-gather

- `check_left.py`: which planned moves did not happen, grouped by reason. Read-only.
- `check_phone.py`: are the planned Phone Link files already in All-Documents (same size)? Read-only.
- `clear_failed_phone.py`: remove the empty, read-only files that the failed 2026-11-12 copy2 attempts
- `debug_one.py`: find out which step of copying one Phone Link file is refused.
- `fold_in.py`: fold the leftover document folders in www into All-Documents; exact copies go to the
- `fold_loose_docs.py`: move the last loose documents outside All-Documents into it (2026-11-12, Claude).
- `hardlink_tree.py`: hardlink_tree.py SRC DST: copy a folder tree as hard links (same drive, no extra space).
- `merge_temp.py`: empty ai-stack\temp into All-Documents. Nothing is deleted.
- `mirror_reorg.py`: repeat the PC's All-Documents reorganisation on the NUC copy and in Paperless.
- `recycle_reviewed.ps1`: recycle_reviewed.ps1 <list.txt> <log.csv>
- `reorg.py`: flatten All-Documents into clear folders and date-first file names.
- `set_file_dates.ps1`: make each document's Windows "Created" and "Modified" times its original date.
- `sort_new.py`: sort the 2026-11-12 Google Drive download into All-Documents by type. Nothing is deleted.

## pc/pc-cleanup

- `autoclick.ps1`: (no header comment yet)
- `compact-docker-disk.ps1`: (no header comment yet)
- `finish-setup.ps1`: (no header comment yet)
- `fix-nuc-key.ps1`: - run as administrator (2026-11-04). install-pc-maint.ps1 appended the NUC's restricted key onto
- `games-off.ps1`: (no header comment yet)
- `gpu-rotation.ps1`: (no header comment yet)
- `install-pc-maint.ps1`: (no header comment yet)
- `list-software.ps1`: (no header comment yet)
- `pc-maint-remote.ps1`: (no header comment yet)
- `pc-maintenance.ps1`: (no header comment yet)
- `quiet-services.ps1`: (no header comment yet)
- `remove-php-from-c-root.ps1`: (no header comment yet)
- `restore-points.ps1`: (no header comment yet)
- `round2.ps1`: (no header comment yet)
- `run-admin-steps.ps1`: - the three admin steps in one elevated window (2026-11-04). Output of each
- `run-uninstall-and-games.ps1`: (no header comment yet)
- `schedule-finish-setup.ps1`: - run ONCE, as administrator, right before you reboot:
- `setup-gpu-rotation.ps1`: (no header comment yet)
- `sort-files.ps1`: (no header comment yet)
- `ssh-key-check.ps1`: - read-only. Shows how Windows' SSH server reads administrators_authorized_keys
- `uninstall-the-rest.ps1`: (no header comment yet)
- `verify-uninstalls.ps1`: (no header comment yet)
- `winget-update.ps1`: (no header comment yet)

## pc/secrets-tools

- `add_todo_route.py`: inserts the /todo SSO-gated static-file route into the NUC's live Caddyfile,
- `auto-resume-claude.ps1`: opens a terminal in the project folder with Claude Code resumed.
- `cleanup-tokens.bat`: list Proton Pass PATs, then delete burned + duplicate ones.
- `cleanup-tokens.py`: list and delete burned/duplicate Proton Pass PATs.
- `compose_config_hash.py`: prove a .env change resolves to the same compose config, without ever
- `grant_vaults.sh`: Run ON THE NUC (Termius works). Gives access tokens Editor on the secrets page's vaults.
- `render_todo_page.py`: reads the Proton Pass "To Do" and "Current Project" notes and renders
- `rotate_ai_stack_env.py`: move every secret in ai-stack's .env into Proton Pass ("API keys"
- `rotate_safe_secrets.py`: rotate a fixed list of self-generated, local-only secrets through
- `watch-and-sync-to-nas.ps1`: watches the project folder and mirrors any change to the NAS

## pc/www-root

- `pc-firewall-undo.ps1`: =============================================================================
- `pc-firewall.ps1`: =============================================================================

## pi/tools

- `qbt_fix.py`: give the Pi's qBittorrent a permanent WebUI password kept in Proton Pass (T0872, 2026-11-18).

## tools

- `after-reboot.ps1`: bring the /backlog feed and page back after a restart, then log the result.
- `break_finder.py`: Break-finder (T1012): list every file or folder this machine's jobs depend on, and say which are missing.
- `broadview_batches.py`: Turn broadview.json (broadview_parse.py) into Actual import batches (2026-11-17, money import).
- `broadview_parse.py`: Parse Broadview FCU eStatement PDFs into transactions, and prove each month adds up (2026-11-17, money import).
- `ci_scan_pin.py`: pin the ci_scan network subnet and trust both host gateways in gitea-proxy (T0630, 2026-11-18).
- `close_items_20261118.py`: Close finished to-do items: tick, add Done/Review lines, move the block to TODO-DONE.md (2026-11-18).
- `code_versions.py`: keep two months of compressed code versions on D: (2026-11-19).
- `config_export_pull.py`: pull the NUC's secret-free config export into the Scratchpad and commit it.
- `daily_github_push.ps1`: (no header comment yet)
- `daily_public_push.ps1`: (no header comment yet)
- `dedupe_media.py`: set aside duplicate media files (2026-11-14, Claude, for Jason).
- `dependabot_midnight.py`: move every Dependabot schedule to 00:00 New York time (2026-11-19).
- `dependabot_one_pr.py`: rewrite a dependabot.yml so ALL version updates arrive as ONE monthly PR (2026-11-19).
- `docker_trim.ps1`: (no header comment yet)
- `done_scan.sh`: scan ONLY the files of a finished project (T0630, Jason 2026-11-18).
- `folder_inventory.py`: read-only list of every top folder on this machine, one CSV row each (T0002 step 1).
- `github_fixes_in.py`: bring Dependabot fixes from the PUBLIC copy back into the PRIVATE Scratchpad (T0870).
- `kopia_add_d_sources.ps1`: add the D:\ folders that were in no backup to the PC's KopiaUI repo
- `litellm_keys.ps1`: (no header comment yet)
- `manga.py`: the scratchpad as manga-style flip books, one PDF per function zone, for Kavita.
- `nas_move.py`: T1014 phase 2: carry out a move list ON the Synology NAS (Python 3.8), journaled, restartable.
- `nas_restore.py`: T1014: undo nas_move.py. Puts every journaled file back where it was. Run ON the NAS.
- `nas_sha.py`: T1014: full SHA-256 of a list of files, run ON the Synology NAS (Python 3.8). Read-only.
- `nas_verify.py`: T1014 phase 2 check, run ON the NAS (Python 3.8). Read-only.
- `ngrams_fetch.sh`: T1018 (Jason 11-18: "do the recommended"): rebuild ~/languagetool-ngrams, lost in the 11-15 wipe.
- `pc_health_admin_setup.ps1`: (no header comment yet)
- `pc_health_log.ps1`: (no header comment yet)
- `pc_images_to_nuc.ps1`: send the PC's running containers to the NUC for the Tuesday update email (2026-11-18).
- `photo_meta.py`: small, dependency-free helpers for the T1014 photo unify plan (unify_photos.py).
- `photos_to_nas.ps1`: (no header comment yet)
- `prescan_tags.sh`: tag every not-yet-worked-on project for a Gitea pre-scan (T0630, Jason 2026-11-18:
- `public_backport.py`: bring changes merged on the PUBLIC GitHub repo back into the private Scratchpad.
- `public_copy.py`: copy the Scratchpad's tracked CODE into D:/scratchpad-public with personal data replaced.
- `py_versions.py`: which Python every script and container runs on, and what blocks moving it up.
- `raw_check_hold.py`: T0005: move the 57 'raw-check' RAWs that have a same-name JPG into the holding folder.
- `raw_to_jpg.py`: T1014 phase 2: convert the RAW-only shots (action raw-convert) to JPG, keeping date + EXIF.
- `scratch.py`: the scratchpad is the master copy of every script Jason runs his site with.
- `seal_secrets.ps1`: (no header comment yet)
- `secret_scan.py`: find files that hold secret VALUES in plain text. Prints paths and kinds, never values.
- `site_smoke.ps1`: (no header comment yet)
- `skills_prompt.py`: pick review/design skills by number for ONE to-do scope, and get a copy-paste prompt.
- `start_check.ps1`: (no header comment yet)
- `stirling_31_patch.py`: move Stirling-PDF to 3.1.0 with household SSO (2026-11-18).
- `sync_main.sh`: bring `main` up to date in every clone on this PC, fast-forward only (2026-11-19).
- `t1014_spurts.py`: T1014 phase 2: run nas_move.py on the NAS in 15-minute spurts until the list is done, and
- `test_nas_move.py`: nas_move.py + nas_restore.py on temp folders (the rename path; the NAS reflink path was
- `test_photo_meta.py`: unit tests for photo_meta.py (T1014). Run: python D:\scratchpad\tools\test_photo_meta.py
- `test_unify_plan.py`: checks unify_plan.build() on fake rows (no NAS needed). Run: python test_unify_plan.py
- `todo_api.py`: the /todo page: ONE unified to-do list, kept in a plain file (2026-11-16, v2).
- `todo_next.py`: todo_next.py (T1017): print the ONE prompt that moves a to-do item to its next part.
- `todo_renumber.py`: order the whole to-do list by what comes next, then renumber T0001.. in that order.
- `todo_renumber_pc.py`: apply the 2026-11-18 to-do renumber map to the PC-side files that name T-numbers.
- `unified_backup.py`: one unified copy of the whole site (PC, NUC, Pi, Garden for All) on the NAS, never overwriting.
- `unify_ops.py`: T1014 phase 2: turn unify-plan.csv into the NAS move list (ops.jsonl). Changes no file.
- `unify_photos.py`: T1014 phase 1 (READ-ONLY): inventory, hash and date every photo on P:, Z: and the M:-only files.
- `unify_plan.py`: T1014 phase 1: turn the cached inventory/hashes/dates into a DRY-RUN plan. Changes no photo.
- `verify_photos.py`: prove which files on M:\Pictures already have an identical copy on the NAS. Deletes nothing.

## tools/backlog

- `push-feed-changed.sh`: copy only the CHANGED files of the PC's feed/ to the NUC backlog page, over SSH.

## tools/ci

- `findings.py`: turn one lint or audit tool's output into the shared findings.json the NUC pulls (2026-11-17).

## tools/copy

- `Backup-ToNas.ps1`: (no header comment yet)
- `Copy-Verified.ps1`: (no header comment yet)
- `Install-SendTo.ps1`: (no header comment yet)
- `Register-CopyTasks.ps1`: (no header comment yet)
- `SendTo-TeraCopy.ps1`: (no header comment yet)
- `Set-TeraCopyReports.ps1`: (no header comment yet)

## tools/nuc

- `caddy_keys_to_env.py`: Move the three Caddy shared keys out of Caddyfile.nuc into web/caddy.nuc.env.
- `rotate_backlog_key.py`: Rotate the Caddy -> backlog shared key (X-Backlog-Key). T0023 step 1, 2026-11-18 autopilot.
- `rotate_garden_dbadmin.py`: Rotate the Garden for All MariaDB password for user dbadmin. T0024 step 2-6, 2026-11-19 autopilot.
- `rotate_notes_key_env.py`: Rotate the Caddy -> notes_api house key (X-Notes-Key), env-file way. T0023, 2026-11-18 autopilot.
