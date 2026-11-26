# The Scratchpad Catalog (public edition)

*Edition of 2026-11-24. Every script in this repo, one page per topic.*

## On the cover

This is a guide to the scripts in this repo: what each one does, how to run it, which machine it runs on, when it last changed, and whether it has its own write-up. Browse by chapter below, or open `catalog/index.html` for the magazine version with a search box.

- **594 scripts** in 9 chapters
- **326** on the PC, **219** on the NUC, **27** on the Pi, **22** on the NAS
- **296** have their own doc, **298** have only their header

How to read an entry: the first line is the script's own header line (the same line as in TOOLS.md). *Run* is the usage line from the header; when the header has none it says so. Regenerate with `py -3.14 -I tools/make_catalog.py`.

## Contents

- [01 Sign-in and People](#01-sign-in-and-people): 5 scripts
- [02 Secrets and Proton Pass](#02-secrets-and-proton-pass): 8 scripts
- [03 Hermes and AI](#03-hermes-and-ai): 47 scripts
- [05 Documents and Paperless](#05-documents-and-paperless): 7 scripts
- [06 Website Pages](#06-website-pages): 304 scripts
- [07 Developer Tools](#07-developer-tools): 32 scripts
- [08 PC Care](#08-pc-care): 31 scripts
- [09 Operations and Backups](#09-operations-and-backups): 89 scripts
- [10 Workshop](#10-workshop): 71 scripts

---

## 01 Sign-in and People

*One sign-in for every page on the site, and the lists of who may use what. These scripts set up the sign-in service, register apps with it, and keep the people lists in step.*

### `auth-setup.ps1`

=============================================================================

- **Run:** `.\auth-setup.ps1`
- **Where:** PC · **File:** `pc/ai-stack/auth-setup.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `people-mirror.ps1`

keeps the PC's copy of the sign-in account list the same as the NUC's.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/people-mirror.ps1`
- **Last changed:** 2026-11-24 · **Documented:** yes

### `register_oidc_client.py`

Register one OIDC client with the household issuer, without printing it.

- **Run:** `python register_oidc_client.py wikijs https://wiki.../login/<uuid>/callback`
- **Where:** PC · **File:** `pc/ai-stack/scripts/register_oidc_client.py`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `identity.py`

Who is talking to Hermes, on whichever surface they are talking from.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/identity.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `invites.py`

Invite links for the guest bot: one tap to join, nothing to transcribe.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/invites.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

---

## 02 Secrets and Proton Pass

*Passwords and keys live in Proton Pass, never in plain text. These scripts rotate keys, move values into the vault, and scan the code for anything that slipped through.*

### `env-sync.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/env-sync.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `litellm_keys.ps1`

give each person/program its own LiteLLM key (models + the site_tools MCP tools),

- **Run:** `powershell -File litellm_keys.ps1`
- **Where:** PC · **File:** `tools/litellm_keys.ps1`
- **Last changed:** 2026-11-16 · **Documented:** no

### `caddy_keys_to_env.py`

Move the three Caddy shared keys out of Caddyfile.nuc into web/caddy.nuc.env.

- **Run:** `caddy_keys_to_env.py check`
- **Where:** NUC · **File:** `tools/nuc/caddy_keys_to_env.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `rotate_backlog_key.py`

Rotate the Caddy -> backlog shared key (X-Backlog-Key). T0023 step 1, 2026-11-18 autopilot.

- **Run:** `rotate_backlog_key.py`
- **Where:** NUC · **File:** `tools/nuc/rotate_backlog_key.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `rotate_garden_dbadmin.py`

Rotate the Robin's Garden MariaDB password for user dbadmin. T0024 step 2-6, 2026-11-19 autopilot.

- **Run:** `rotate_garden_dbadmin.py`
- **Where:** NUC · **File:** `tools/nuc/rotate_garden_dbadmin.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `rotate_notes_key_env.py`

Rotate the Caddy -> notes_api house key (X-Notes-Key), env-file way. T0023, 2026-11-18 autopilot.

- **Run:** `~/learning-src/.notes-key`
- **Where:** NUC · **File:** `tools/nuc/rotate_notes_key_env.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `seal_secrets.ps1`

put plain-text secret files into ONE encrypted 7-Zip archive, then remove the

- **Run:** `powershell -File seal_secrets.ps1`
- **Where:** PC · **File:** `tools/seal_secrets.ps1`
- **Last changed:** 2026-11-16 · **Documented:** no

### `secret_scan.py`

find files that hold secret VALUES in plain text. Prints paths and kinds, never values.

- **Run:** `py -3.14 -I secret_scan.py [ROOT ...]`
- **Where:** PC · **File:** `tools/secret_scan.py`
- **Last changed:** 2026-11-19 · **Documented:** no

---

## 03 Hermes and AI

*Hermes is the house AI assistant, backed by local models on the PC's GPU. Here are its tools, its shared memory and routing code, the voice and speech services, and the model helpers.*

### `Get-Model.ps1`

.SYNOPSIS

- **Run:** `.\Get-Model.ps1`
- **Where:** PC · **File:** `pc/ai-stack/colibri/Get-Model.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `install-resume-startup.ps1`

.SYNOPSIS

- **Run:** `.\install-resume-startup.ps1`
- **Where:** PC · **File:** `pc/ai-stack/colibri/install-resume-startup.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `install-resume-task.ps1`

.SYNOPSIS

- **Run:** `.\install-resume-task.ps1`
- **Where:** PC · **File:** `pc/ai-stack/colibri/install-resume-task.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pull-dsv41.ps1`

.SYNOPSIS

- **Run:** `.\pull-dsv41.ps1`
- **Where:** PC · **File:** `pc/ai-stack/colibri/pull-dsv41.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `resume-dsv41.ps1`

.SYNOPSIS

- **Run:** `.\resume-dsv41.ps1`
- **Where:** PC · **File:** `pc/ai-stack/colibri/resume-dsv41.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `resume-once.ps1`

One check, then exit. Run by the Scheduled Task every 15 minutes.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/colibri/resume-once.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `Watch-Model.ps1`

.SYNOPSIS

- **Run:** `.\Watch-Model.ps1`
- **Where:** PC · **File:** `pc/ai-stack/colibri/Watch-Model.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `hermes-launch.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hermes-launch.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `__init__.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hermes/app/__init__.py`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `agent.py`

The Hermes agent loop: model -> tool calls -> results -> model, until it

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hermes/app/agent.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `api_tools.py`

Free plant and web-search API tools for Hermes (all READ-ONLY).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hermes/app/api_tools.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `cli.py`

Hermes CLI - the terminal front end.

- **Run:** `docker compose exec hermes hermes`
- **Where:** PC · **File:** `pc/ai-stack/hermes/app/cli.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `doc_tools.py`

Knowledge-store tools for Hermes: search the documents saved in Paperless.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hermes/app/doc_tools.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `house.py`

The house tools -- Hermes stops being a stranger in its own home.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hermes/app/house.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `memory_tools.py`

The memory tools, bound to ONE person's store.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hermes/app/memory_tools.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `plant_tools.py`

Plant lookup tools for Hermes.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hermes/app/plant_tools.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `server.py`

Hermes over HTTP, so a phone can talk to the same assistant the CLI does.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hermes/app/server.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `speak.py`

Say the same thing the way each person actually reads.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hermes/app/speak.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `threads.py`

The conversation each person left off in, kept on the server.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hermes/app/threads.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `tools.py`

Hermes tool belt.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hermes/app/tools.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `ui.py`

How Hermes looks in the terminal.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hermes/app/ui.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `load_github_facts.py`

Load GitHub facts into one person's Hermes memory, through Hermes' own MemoryStore.

- **Run:** `docker exec -i hermes python /app/load_github_facts.py [email] < facts.json`
- **Where:** PC · **File:** `pc/ai-stack/hermes/load_github_facts.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `model-bench.py`

Can gemma4:12b replace five models on this card?

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/litellm/model-bench.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `__init__.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/__init__.py`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `distil.py`

Turn a person's activity log into facts worth keeping.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/distil.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `health.py`

One screen that answers "is anything wrong right now".

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/health.py`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `history.py`

Turning a month of samples into something you can read.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/history.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `memory.py`

Per-person memory for Hermes: markdown files in a directory that person owns.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/memory.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `models_health.py`

Which job routes actually work, and which are quietly broken.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/models_health.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `offers.py`

Ask once, keep it everywhere: the household's shared yes/no queue.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/offers.py`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `printing.py`

The print server's brain: everything that decides, nothing that touches a

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/printing.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `report.py`

The 8pm report: one message, once a day, that you actually read.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/report.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `router.py`

Thin client for the LiteLLM router.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/router.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `stats.py`

/stats - one command, two depths.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/stats.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `transcript.py`

Shared transcript log.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/transcript.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `usage.py`

GPU and LLM usage, in one place, for both Hermes and the Telegram bot.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/usage.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `voice.py`

Client for the cloned-voice TTS service.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/shared/voice.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `server.py`

Local speech-to-text. Whisper on your GPU, two ways in.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/stt/server.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `bot.py`

Telegram bridge.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/telegram/bot.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `narrate.py`

Turn a document into an audiobook in one of the house's cloned voices.

- **Run:** `python narrate.py "path/to/book.pdf" --voice Mom --out [path]`
- **Where:** PC · **File:** `pc/ai-stack/voice/narrate.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `narrate_worker.py`

Pick up queued documents and read them aloud into the audiobook shelf.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/voice/narrate_worker.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `server.py`

The voice. One service, three consumers, one copy of the weights in VRAM.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/voice/server.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `transcribe-references.py`

Write an accurate transcript beside every reference clip.

- **Run:** `python transcribe-references.py`
- **Where:** PC · **File:** `pc/ai-stack/voice/transcribe-references.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `trim-references.py`

Cut every voice reference down to the window the models actually want.

- **Run:** `python trim-references.py`
- **Where:** PC · **File:** `pc/ai-stack/voice/trim-references.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `voices.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/voices.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `claude_config_sync.py`

copy Claude's memory and skills into [path] and commit, so they get a history.

- **Run:** `py -3.14 -I [path]`
- **Where:** PC · **File:** `tools/claude_config_sync.py`
- **Last changed:** 2026-11-21 · **Documented:** no

### `skills_prompt.py`

pick review/design skills by number for ONE to-do scope, and get a copy-paste prompt.

- **Run:** `python3 ~/learning-src/skills_prompt.py menu [tab]`
- **Where:** PC · **File:** `tools/skills_prompt.py`
- **Last changed:** 2026-11-19 · **Documented:** no

---

## 05 Documents and Paperless

*Paper and digital documents end up in Paperless, sorted and searchable. These scripts gather documents from the PC and phone, sort them, and load them in.*

### `audio_files.py`

keep every website audio file in web/public/Audio-Files, with links at the old

- **Run:** `python3 audio_files.py <web/public> plan`
- **Where:** PC · **File:** `pc/ai-stack/scripts/audio_files.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `paperless_load.py`

Load text documents into Paperless so Hermes can search them.

- **Run:** `python3 paperless_load.py <folder> <source-tag> [--dry-run]`
- **Where:** PC · **File:** `pc/ai-stack/scripts/paperless_load.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `publish_documents.py`

turn All-Documents\Projects\Homelab Docs (was Documents\Homelab Docs) into pages on the site (2026-10-31).

- **Run:** `python publish_documents.py`
- **Where:** PC · **File:** `pc/ai-stack/scripts/publish_documents.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `sync_documents.py`

Nightly: new and changed documents in All-Documents go to the NUC and into Paperless.

- **Run:** `~/documents/jason/All-Documents/ . That folder is inside the mounts the`
- **Where:** PC · **File:** `pc/ai-stack/scripts/sync_documents.py`
- **Last changed:** 2026-11-22 · **Documented:** yes

### `tidy_watch.py`

keep All-Documents and image-files sorted as Jason drops new files in.

- **Run:** `python -I scripts/tidy_watch.py`
- **Where:** PC · **File:** `pc/ai-stack/scripts/tidy_watch.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `doc_gather.py`

Move (or copy) www documents into [path] (T0936 step 1b, 2026-11-20).

- **Run:** `py -3.14 -I -X utf8 doc_gather.py INVENTORY.csv [--write]`
- **Where:** PC · **File:** `tools/doc_gather.py`
- **Last changed:** 2026-11-20 · **Documented:** no

### `doc_inventory.py`

Read-only: document files under [path] New Site\www outside All-Documents (T0936 step 1b, 2026-11-20).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/doc_inventory.py`
- **Last changed:** 2026-11-20 · **Documented:** no

---

## 06 Website Pages

*Everything a visitor can open in a browser: Novashare file drops, the garden, the studio, the wiki and the small apps behind each page. Most run in Docker behind Caddy on the NUC or the PC.*

### `abyss_patch.py`

Novashare side of Abyss step 5 (2026-11-24): "Send as Abyss" + the Abyss row in the gateway table.

- **Run:** `python3 abyss_patch.py <copy of ~/nuc-homelab/intake>`
- **Where:** NUC · **File:** `nuc/intake/abyss_patch.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `app.py`

The intake door. n8n hands it a file; it decides and files it.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/app.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `atomic_patch.py`

wire resumable.py + atomic_ui.py into intake (T0884, 2026-11-18). Run inside a COPY of

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/atomic_patch.py`
- **Last changed:** 2026-11-18 · **Documented:** yes

### `atomic_ui.py`

Atomic Share on the phone: the service worker and the "sending" page (T0884 step 1, 2026-11-18).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/atomic_ui.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `captcha_patch.py`

share.py: refuse to file a CAPTCHA / bot-check page as if it were the shared page (2026-11-18).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/captcha_patch.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `classify.py`

Decide what an arriving file IS and who it belongs to. Nothing else.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/classify.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `dropconfig.py`

Atomic Drop / Garden Drop settings from ONE file instead of tables in the code (T0927 slice 1).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/dropconfig.py`
- **Last changed:** 2026-11-22 · **Documented:** yes

### `gemini_patch.py`

teach share.py about Gemini share links (2026-11-18). Run on the NUC in ~/nuc-homelab/intake.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/gemini_patch.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `gemini_probe.py`

one-off: what does a Gemini share URL look like to a plain fetch and to gotenberg-web? (2026-11-18)

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/gemini_probe.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `gemini_sbx.sh`

sandbox test of the share door's link handling: a dead Gemini share and a Google CAPTCHA page

- **Run:** `sh ~/nuc-homelab/intake/gemini_sbx.sh [real-gemini-share-url ...]`
- **Where:** NUC · **File:** `nuc/intake/gemini_sbx.sh`
- **Last changed:** 2026-11-22 · **Documented:** yes

### `gen_icons.py`

draw the Atomic Share atom (same as share.ICON) as PNG 192 and 512 and write icons_png.py (2026-11-18).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/gen_icons.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `gen_icons_horizon.py`

the Novashare app icon (theme "horizon", 2026-11-23): a black hole with a bright photon

- **Run:** `python gen_icons_horizon.py icons_png_horizon.py`
- **Where:** NUC · **File:** `nuc/intake/gen_icons_horizon.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `gotenberg_web_patch.py`

add a separate Gotenberg for printing shared links to PDF (T0872, 2026-11-18).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/gotenberg_web_patch.py`
- **Last changed:** 2026-11-18 · **Documented:** yes

### `icons_png.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/icons_png.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `icons_png_garden.py`

the Garden Drop icon (SVG + PNG), made by gen_icons_garden.py (T0942). Do not edit.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/icons_png_garden.py`
- **Last changed:** 2026-11-22 · **Documented:** yes

### `icons_png_horizon.py`

the Novashare icon (SVG + PNG), made by gen_icons_horizon.py. Do not edit.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/icons_png_horizon.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `intake_regress_live.sh`

every intake test against throw-away sandboxes built from a FRESH copy of the LIVE

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/intake_regress_live.sh`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `link_probe.py`

Header-only look at a shared link: what is it (Content-Type, size, file name) before anything is fetched.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/link_probe.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `link_route.py`

Shared links that are YouTube videos or songs -> MeTube (yt-dlp) instead of a PDF of the page.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/link_route.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `make_drop_config.py`

write the first drop.toml from the people tables now in the intake code (T0927 slice 1).

- **Run:** `python3 make_drop_config.py SRC_DIR OUT_FILE`
- **Where:** NUC · **File:** `nuc/intake/make_drop_config.py`
- **Last changed:** 2026-11-21 · **Documented:** yes

### `devices.py`

Novashare person-to-person E2E, Step 1: the device registry (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step1/devices.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `devices_js.py`

the Novashare Devices page script (E2E Step 1, 2026-11-24), served by devices.py.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step1/devices_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `devices_sbx.sh`

Novashare E2E Step 1 sandbox (2026-11-24): build + test a PATCHED COPY of live intake. Never swaps.

- **Run:** `bash ~/sandbox/novashare-devices/devices_sbx.sh up`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step1/devices_sbx.sh`
- **Last changed:** 2026-11-24 · **Documented:** no

### `novashare_devices_patch.py`

Novashare E2E Step 1 patcher (2026-11-24), for ~/sandbox/horizon/SWAP-PATCH.sh.

- **Run:** `python3 novashare_devices_patch.py INTAKE_COPY_DIR`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step1/novashare_devices_patch.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_devices.py`

API proof for devices.py (Novashare E2E Step 1, 2026-11-24). Self-contained: run INSIDE the

- **Run:** `docker run --rm --entrypoint python IMAGE test_devices.py`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step1/test_devices.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_devices_browser.py`

Novashare E2E Step 1 in real Chrome (2026-11-24): two people, separate browser profiles.

- **Run:** `bash ~/sandbox/novashare-devices/devices_sbx.sh up`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step1/test_devices_browser.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `vendor_noble_pq.py`

the vendored ML-KEM-768 bundle for the Novashare device keys (E2E Step 1, 2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step1/vendor_noble_pq.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `novashare_relay_patch.py`

Novashare E2E Step 2 patcher (2026-11-24), for ~/sandbox/horizon/SWAP-PATCH.sh.

- **Run:** `python3 novashare_relay_patch.py INTAKE_COPY_DIR`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step2/novashare_relay_patch.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `relay.py`

Novashare person-to-person E2E, Step 2: the encrypted send (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step2/relay.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `relay_js.py`

the Novashare Send page script (E2E Step 2, 2026-11-24), served by relay.py at /relay/static/relay.js.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step2/relay_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `relay_sbx.sh`

Novashare E2E Step 2 sandbox (2026-11-24): build + test a PATCHED COPY of live intake. Never swaps.

- **Run:** `bash ~/sandbox/novashare-relay/relay_sbx.sh test`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step2/relay_sbx.sh`
- **Last changed:** 2026-11-24 · **Documented:** no

### `relay_twin.py`

TEST-ONLY Python twin of the Novashare Step 2 crypto (2026-11-24). Never installed in the live app

- **Run:** `python relay_twin.py register BASE EMAIL LABEL KEYFILE`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step2/relay_twin.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_relay.py`

server proof for relay.py (Novashare E2E Step 2, 2026-11-24). Self-contained: run INSIDE the

- **Run:** `docker run --rm --entrypoint python IMAGE test_relay.py`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step2/test_relay.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_relay_browser.py`

Novashare E2E Step 2 in real Chrome (2026-11-24): the browser's own encrypted send.

- **Run:** `bash ~/sandbox/novashare-relay/relay_sbx.sh up`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step2/test_relay_browser.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_relay_e2e.py`

Novashare E2E Step 2 end to end (2026-11-24): real server, real crypto, 2 GB.

- **Run:** `docker run --rm --entrypoint python intake-sandbox:relay-tester test_relay_e2e.py [--big-mb 2048]`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step2/test_relay_e2e.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `novashare_receive_patch.py`

Novashare E2E Step 3 patcher (2026-11-24), for ~/sandbox/horizon/SWAP-PATCH.sh.

- **Run:** `python3 novashare_receive_patch.py INTAKE_COPY_DIR`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step3/novashare_receive_patch.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `receive.py`

Novashare person-to-person E2E, Step 3: receive (inbox, Accept / Decline, pieces) (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step3/receive.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `receive_js.py`

the Novashare Inbox page script, style and download service worker (E2E Step 3, 2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step3/receive_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `receive_sbx.sh`

Novashare E2E Step 3 sandbox (2026-11-24): build + test a PATCHED COPY of live intake. Never swaps.

- **Run:** `bash ~/sandbox/novashare-receive/receive_sbx.sh test`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step3/receive_sbx.sh`
- **Last changed:** 2026-11-24 · **Documented:** no

### `relay.py`

Novashare person-to-person E2E, Step 2: the encrypted send (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step3/relay.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `relay_js.py`

the Novashare Send page script (E2E Step 2, 2026-11-24), served by relay.py at /relay/static/relay.js.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step3/relay_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_receive.py`

server proof for receive.py (Novashare E2E Step 3, 2026-11-24). Self-contained: run INSIDE the

- **Run:** `docker run --rm --entrypoint python IMAGE test_receive.py`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step3/test_receive.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_receive_browser.py`

Novashare E2E Step 3 in real Chrome (2026-11-24): two people, two devices, round trip.

- **Run:** `python test_receive_browser.py WORK_DIR [--big-mb 2048]`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step3/test_receive_browser.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `devices.py`

Novashare person-to-person E2E, Step 1: the device registry (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step4/devices.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `make_vapid_key.sh`

Novashare E2E Step 4 (2026-11-24): make the VAPID key pair ONCE and keep the private key in

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step4/make_vapid_key.sh`
- **Last changed:** 2026-11-24 · **Documented:** no

### `mock_push.py`

a local stand-in for a web push service (Novashare E2E Step 4 sandbox, 2026-11-24). Test only.

- **Run:** `python mock_push.py [PORT]`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step4/mock_push.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `novashare_push_patch.py`

Novashare E2E Step 4 patcher (2026-11-24), for ~/sandbox/horizon/SWAP-PATCH.sh.

- **Run:** `python3 novashare_push_patch.py INTAKE_COPY_DIR`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step4/novashare_push_patch.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `push.py`

Novashare person-to-person E2E, Step 4: web push notifications (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step4/push.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `push_js.py`

the Novashare "Notify me on this device" script (E2E Step 4, 2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step4/push_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `push_sbx.sh`

Novashare E2E Step 4 sandbox (2026-11-24): build + test a PATCHED COPY of live intake. Never swaps.

- **Run:** `bash ~/sandbox/novashare-push/push_sbx.sh test`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step4/push_sbx.sh`
- **Last changed:** 2026-11-24 · **Documented:** no

### `push_swap.sh`

Novashare E2E Step 4 swap (2026-11-24). Run ONLY when Jason says so:

- **Run:** `bash ~/sandbox/novashare-push/push_swap.sh`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step4/push_swap.sh`
- **Last changed:** 2026-11-24 · **Documented:** no

### `receive.py`

Novashare person-to-person E2E, Step 3: receive (inbox, Accept / Decline, pieces) (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step4/receive.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `receive_js.py`

the Novashare Inbox page script, style and download service worker (E2E Step 3, 2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step4/receive_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_push.py`

server proof for push.py (Novashare E2E Step 4, 2026-11-24). Self-contained: run INSIDE the patched

- **Run:** `docker run --rm --entrypoint python IMAGE test_push.py`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step4/test_push.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_push_e2e.py`

Novashare E2E Step 4 sandbox end-to-end (2026-11-24). Run by push_sbx.sh e2e INSIDE the running

- **Run:** `python test_push_e2e.py http://sbx-novashare-push-mock:8099`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step4/test_push_e2e.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `audit.py`

the Novashare relay audit log, without content (E2E Step 6, 2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step6/audit.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `devices.py`

Novashare person-to-person E2E, Step 1: the device registry (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step6/devices.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `novashare_sweep_patch.py`

Novashare E2E Step 6 patcher (2026-11-24), for ~/sandbox/horizon/SWAP-PATCH.sh.

- **Run:** `python3 novashare_sweep_patch.py INTAKE_COPY_DIR`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step6/novashare_sweep_patch.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `push.py`

Novashare person-to-person E2E, Step 4: web push notifications (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step6/push.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `receive.py`

Novashare person-to-person E2E, Step 3: receive (inbox, Accept / Decline, pieces) (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step6/receive.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `receive_js.py`

the Novashare Inbox page script, style and download service worker (E2E Step 3, 2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step6/receive_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `relay.py`

Novashare person-to-person E2E, Step 2: the encrypted send (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step6/relay.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `relay_js.py`

the Novashare Send page script (E2E Step 2, 2026-11-24), served by relay.py at /relay/static/relay.js.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step6/relay_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `sweep.py`

Novashare person-to-person E2E, Step 6: expiry, quotas, sender cancel, audit log (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step6/sweep.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `sweep_sbx.sh`

Novashare E2E Step 6 sandbox (2026-11-24): build + test a PATCHED COPY of live intake. Never swaps.

- **Run:** `bash ~/sandbox/novashare-sweep/sweep_sbx.sh test`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step6/sweep_sbx.sh`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_sweep.py`

server proof for Novashare E2E Step 6 (sweep.py, audit.py, and the Step 6 changes in relay.py,

- **Run:** `docker run --rm --entrypoint python IMAGE test_sweep.py`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step6/test_sweep.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_sweep_browser.py`

Novashare E2E Step 6 in real Chrome, two people (2026-11-24): cancel, quotas, Relay line.

- **Run:** `python test_sweep_browser.py WORK_DIR`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step6/test_sweep_browser.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `abyss_keyring_js.py`

the Novashare Keyring page script (2026-11-24), served by abyss_keyring.py at /keyring/static/keyring.js.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/abyss_keyring_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `app.py`

The intake door. n8n hands it a file; it decides and files it.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/app.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `atomic_ui.py`

Atomic Share on the phone: the service worker and the "sending" page (T0884 step 1, 2026-11-18).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/atomic_ui.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `devices.py`

Novashare person-to-person E2E, Step 1: the device registry (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/devices.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `devices_js.py`

the Novashare Devices page script (E2E Step 1, 2026-11-24), served by devices.py.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/devices_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `gateway.py`

Novashare as the gateway: one table of where every kind of share goes, with live health (2026-11-23).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/gateway.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `harden_sbx.sh`

Novashare step 7 sandbox (2026-11-24): build + test a PATCHED COPY of live intake. Never swaps.

- **Run:** `bash ~/sandbox/novashare-harden/harden_sbx.sh test`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/harden_sbx.sh`
- **Last changed:** 2026-11-24 · **Documented:** no

### `harden_swap.sh`

Novashare step 7 swap (2026-11-24, approved by Jason: "approve the fix batch", "burn the old pages").

- **Run:** `bash ~/sandbox/novashare-harden/harden_swap.sh`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/harden_swap.sh`
- **Last changed:** 2026-11-24 · **Documented:** no

### `link_probe.py`

Header-only look at a shared link: what is it (Content-Type, size, file name) before anything is fetched.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/link_probe.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `novashare_harden_patch.py`

Novashare step 7 (2026-11-24): the drop site under one strict CSP + the audit fixes.

- **Run:** `python3 novashare_harden_patch.py TARGET_DIR`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/novashare_harden_patch.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `pick_js.py`

the Novashare share picker's script and style (E2E Step 5, 2026-11-24), served by pick.py.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/pick_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `portal.py`

The drop page. Who you are decides where it goes.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/portal.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `push_js.py`

the Novashare "Notify me on this device" script (E2E Step 4, 2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/push_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `receive.py`

Novashare person-to-person E2E, Step 3: receive (inbox, Accept / Decline, pieces) (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/receive.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `receive_js.py`

the Novashare Inbox page script, style and download service worker (E2E Step 3, 2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/receive_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `relay.py`

Novashare person-to-person E2E, Step 2: the encrypted send (2026-11-24).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/relay.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `relay_js.py`

the Novashare Send page script (E2E Step 2, 2026-11-24), served by relay.py at /relay/static/relay.js.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/relay_js.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `share.py`

Phone "Share" -> Drop. Makes the drop page an installable app that shows up in the phone's share sheet.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/share.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `site_assets.py`

the drop site's own style and script files, and its one strict header set (2026-11-24, step 7).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/site_assets.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_abyss_handoff.py`

Abyss step 5 on the Novashare side (2026-11-24): the Abyss gateway row + health check, and the

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/test_abyss_handoff.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_devices.py`

API proof for devices.py (Novashare E2E Step 1, 2026-11-24). Self-contained: run INSIDE the

- **Run:** `docker run --rm --entrypoint python IMAGE test_devices.py`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/test_devices.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_harden.py`

step 7 (2026-11-24): the whole drop site under one strict CSP, and the audit fixes. Self-contained:

- **Run:** `docker run --rm --entrypoint python IMAGE test_harden.py`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/test_harden.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_harden_browser.py`

Novashare step 7 in real Chrome, two people (2026-11-24): strict CSP on every page,

- **Run:** `python test_harden_browser.py WORK_DIR`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/test_harden_browser.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_pick.py`

server proof for pick.py and relay.py's "only" (Novashare E2E Step 5, 2026-11-24). Self-contained:

- **Run:** `docker run --rm --entrypoint python IMAGE test_pick.py`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/test_pick.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_ui.py`

proof for patch_ui.py (screens review 2026-11-23). Run INSIDE the patched image with a horizon

- **Run:** `docker run --rm -v nova.toml:/config/drop.toml:ro --entrypoint python IMAGE test_ui.py [DUMP_DIR]`
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/test_ui.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `themes.py`

the drop's look, chosen by drop.toml [brand] theme (T0942, 2026-11-21).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/themes.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `trivy_count.sh`

trivy_count.sh IMAGE OUT.json - Trivy 0.58.1 (as the 2026-11-24 audit) HIGH/CRITICAL scan of a local image.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/novashare-e2e-step7/trivy_count.sh`
- **Last changed:** 2026-11-24 · **Documented:** no

### `sendfix_sbx.sh`

Novashare "cannot send" sandbox (2026-11-24). Runs the HARDENED (step 7) intake from a copy of

- **Run:** `bash ~/sandbox/novashare-sendfix/sendfix_sbx.sh up | down`
- **Where:** NUC · **File:** `nuc/intake/novashare-sendfix/sendfix_sbx.sh`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_sendfix_browser.py`

Novashare "cannot send" check (2026-11-24), real Chrome at phone size (412 x 915, touch).

- **Run:** `python test_sendfix_browser.py`
- **Where:** NUC · **File:** `nuc/intake/novashare-sendfix/test_sendfix_browser.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `onion.py`

save a shared Tor (.onion) link, fetched over Tor (2026-11-23).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/onion-2026-11-23/onion.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `patch_onion.py`

Tor (.onion) links in Novashare's /share (2026-11-23), on a COPY of intake.

- **Run:** `python3 patch_onion.py COPY_OF_INTAKE`
- **Where:** NUC · **File:** `nuc/intake/onion-2026-11-23/patch_onion.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `test_onion.py`

proof for onion.py with a REAL Tor proxy. Run INSIDE the patched image on the compose network:

- **Run:** `docker run --rm --network nuc-homelab_default -e TOR_PROXY=socks5h://gluetun-tor:9050 --entrypoint python IMAGE test_onion.py`
- **Where:** NUC · **File:** `nuc/intake/onion-2026-11-23/test_onion.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `patch_people_tables.py`

T0927 slice 1b: in a COPY of intake, replace the people tables in the code with

- **Run:** `python3 patch_people_tables.py ~/sandbox/t0927/intake`
- **Where:** NUC · **File:** `nuc/intake/patch_people_tables.py`
- **Last changed:** 2026-11-21 · **Documented:** yes

### `patch_scan.py`

T0920: in a COPY of intake, call scan.py at the top of app.route_file(). Never on the live folder.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/patch_scan.py`
- **Last changed:** 2026-11-21 · **Documented:** yes

### `portal.py`

The drop page. Who you are decides where it goes.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/portal.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `pwa_patch.py`

share.py: PNG icons so Android can install Atomic Share as a share target (2026-11-18).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/pwa_patch.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `pwa_sbx.sh`

copy-test of the PNG icon patch (2026-11-18). Builds a candidate from a COPY of intake, runs it as

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/pwa_sbx.sh`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `readable.py`

Readable text of a shared web page: the article without menus, ads, scripts or cookie banners.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/readable.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `rescan_held.py`

T0962 point 4: re-check uploads that were held only because the virus scanner was down.

- **Run:** `docker exec intake python rescan_held.py`
- **Where:** NUC · **File:** `nuc/intake/rescan_held.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `resumable.py`

Resumable uploads for Atomic Share: big files survive a dropped signal (T0884 step 1, 2026-11-18).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/resumable.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `patch_locks.py`

clear stale finish locks at SERVER START, not on import (2026-11-23), in a COPY of intake.

- **Run:** `python3 patch_locks.py COPY_OF_INTAKE`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/patch_locks.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `patch_purple.py`

Novashare in black and purple with an event-horizon orange (2026-11-23 evening), on a COPY.

- **Run:** `python3 patch_purple.py COPY_OF_INTAKE NEW_icons_png_horizon.py`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/patch_purple.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `patch_races.py`

race-condition review 2026-11-23 ([path] on a COPY of intake.

- **Run:** `python3 patch_races.py COPY_OF_INTAKE`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/patch_races.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `patch_srvA.py`

Novashare backend review 2026-11-23, batch A, applied to a COPY of intake.

- **Run:** `python3 patch_srvA.py COPY_OF_INTAKE`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/patch_srvA.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `patch_srvB.py`

Novashare backend review 2026-11-23, batch B, applied to a COPY of intake.

- **Run:** `python3 patch_srvB.py COPY_OF_INTAKE`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/patch_srvB.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `patch_srvC.py`

Novashare backend review 2026-11-23, batch C (the three lows, T0970-T0972), on a COPY of intake.

- **Run:** `python3 patch_srvC.py COPY_OF_INTAKE`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/patch_srvC.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `patch_torlink.py`

a "Tor Browser" button for Jason on the Novashare page (2026-11-23), on a COPY of intake.

- **Run:** `python3 patch_torlink.py COPY_OF_INTAKE`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/patch_torlink.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `patch_ui.py`

Novashare screens review 2026-11-23 (frontend.md), applied to a COPY of intake.

- **Run:** `python3 patch_ui.py COPY_OF_INTAKE`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/patch_ui.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `SWAP-PATCH.sh`

generic intake swap (2026-11-23): patch a fresh copy of live, test it, back up, install, rebuild, regress.

- **Run:** `bash ~/sandbox/horizon/SWAP-PATCH.sh TAG PATCHER.py TEST.py`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/SWAP-PATCH.sh`
- **Last changed:** 2026-11-23 · **Documented:** no

### `test_locks.py`

proof for patch_locks.py. Run INSIDE the patched intake image:

- **Run:** `docker run --rm --entrypoint python IMAGE test_locks.py`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/test_locks.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `test_purple.py`

proof for patch_purple.py. Run INSIDE the patched image with a horizon drop.toml mounted:

- **Run:** `docker run --rm -v nova.toml:/config/drop.toml:ro --entrypoint python IMAGE test_purple.py`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/test_purple.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `test_races.py`

proof for patch_races.py (race review 2026-11-23). Run INSIDE the patched intake image:

- **Run:** `docker run --rm --entrypoint python IMAGE test_races.py`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/test_races.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `test_srvA.py`

proof for patch_srvA.py (review 2026-11-23 batch A). Run INSIDE the patched intake image:

- **Run:** `docker run --rm --entrypoint python IMAGE test_srvA.py`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/test_srvA.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `test_srvB.py`

proof for patch_srvB.py (review 2026-11-23 batch B). Run INSIDE the patched intake image:

- **Run:** `docker run --rm --entrypoint python IMAGE test_srvB.py`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/test_srvB.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `test_srvC.py`

proof for patch_srvC.py (review 2026-11-23 #15 #16 #17). Run INSIDE the patched intake image:

- **Run:** `docker run --rm --entrypoint python IMAGE test_srvC.py`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/test_srvC.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `test_torlink.py`

proof for patch_torlink.py: the Tor Browser link shows for the admin (jason) only.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/test_torlink.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `test_ui.py`

proof for patch_ui.py (screens review 2026-11-23). Run INSIDE the patched image with a horizon

- **Run:** `docker run --rm -v nova.toml:/config/drop.toml:ro --entrypoint python IMAGE test_ui.py [DUMP_DIR]`
- **Where:** NUC · **File:** `nuc/intake/review-2026-11-23/test_ui.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `scan.py`

virus check of ONE staged file by ClamAV (clamd) before intake files it (T0920, 2026-11-21).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/scan.py`
- **Last changed:** 2026-11-22 · **Documented:** yes

### `share.py`

Phone "Share" -> Drop. Makes the drop page an installable app that shows up in the phone's share sheet.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/share.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `share_patch.py`

wire share.py into the intake service (T0872, 2026-11-18). Run in the intake folder.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/share_patch.py`
- **Last changed:** 2026-11-18 · **Documented:** yes

### `share_test.sh`

sandbox test of the /share door (T0872). Runs in a curl container on nuc-homelab_default

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/share_test.sh`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `app.py`

The intake door. n8n hands it a file; it decides and files it.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/staging-t0932/app.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `patch_t0932_s2.py`

T0932 step 2 (2026-11-22, autopilot): add a max-staging size check to resumable.py.

- **Run:** `python3 patch_t0932_s2.py <intake folder copy>`
- **Where:** NUC · **File:** `nuc/intake/staging-t0932/patch_t0932_s2.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `patch_t0932_s3.py`

T0932 step 3 (2026-11-22, autopilot): filing across disks copies, CHECKS sha256, then

- **Run:** `python3 patch_t0932_s3.py <intake folder copy>`
- **Where:** NUC · **File:** `nuc/intake/staging-t0932/patch_t0932_s3.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `patch_t0932_s4.py`

T0932 step 4 (2026-11-22, autopilot): check free space on the DESTINATION before a

- **Run:** `python3 patch_t0932_s4.py <intake folder copy>`
- **Where:** NUC · **File:** `nuc/intake/staging-t0932/patch_t0932_s4.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `resumable.py`

Resumable uploads for Atomic Share: big files survive a dropped signal (T0884 step 1, 2026-11-18).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/staging-t0932/resumable.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `t0932_step5_sbx.sh`

T0932 step 5 sandbox (2026-11-22, autopilot): a 2 GB file through the PATCHED intake

- **Run:** `bash t0932_step5_sbx.sh`
- **Where:** NUC · **File:** `nuc/intake/staging-t0932/t0932_step5_sbx.sh`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_t0932_s2.py`

sandbox test for the T0932 step 2 staging cap (2026-11-22). Talks to a SANDBOX intake

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/staging-t0932/test_t0932_s2.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_t0932_s3.py`

T0932 step 3 test for app.place() (2026-11-22). Runs INSIDE a sandbox intake image with

- **Run:** `docker run --rm ... --entrypoint python <image> test_t0932_s3.py`
- **Where:** NUC · **File:** `nuc/intake/staging-t0932/test_t0932_s3.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_t0932_s4.py`

T0932 step 4 test (2026-11-22). Runs INSIDE a sandbox intake image started with

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/staging-t0932/test_t0932_s4.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_t0932_s5.py`

T0932 step 5 (2026-11-22, autopilot): 2 GB upload through the SANDBOX intake whose whole

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/staging-t0932/test_t0932_s5.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_t0932_s5_place.py`

T0932 step 5 (2026-11-22, autopilot): app.place() with a 2 GB file across disks.

- **Run:** `docker exec <sandbox> python test_t0932_s5_place.py /srv/unsorted/<the 2 GB file from the upload test>`
- **Where:** NUC · **File:** `nuc/intake/staging-t0932/test_t0932_s5_place.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `SWAP-T0889.sh`

T0889 step 7 (2026-11-22): put the link router live on the Drop.

- **Run:** `bash ~/sandbox/t0889/SWAP-T0889.sh`
- **Where:** NUC · **File:** `nuc/intake/t0889/SWAP-T0889.sh`
- **Last changed:** 2026-11-22 · **Documented:** no

### `t0889_routes_sbx.sh`

T0889 step 7 (2026-11-22): every shared-link route against REAL sites and the REAL

- **Run:** `bash ~/sandbox/t0889/t0889_routes_sbx.sh`
- **Where:** NUC · **File:** `nuc/intake/t0889/t0889_routes_sbx.sh`
- **Last changed:** 2026-11-22 · **Documented:** no

### `patch_libraries.py`

T0927 slice 2, in a COPY of intake only: libraries as config + required settings.

- **Run:** `python3 patch_libraries.py ~/sandbox/t0927s2/intake`
- **Where:** NUC · **File:** `nuc/intake/t0927s2/patch_libraries.py`
- **Last changed:** 2026-11-21 · **Documented:** no

### `SWAP-T0927s2.sh`

put T0927 slice 2 live: libraries as config (dropconfig.LIBRARIES, defaults = the old tables)

- **Run:** `bash ~/sandbox/t0927s2/SWAP-T0927s2.sh`
- **Where:** NUC · **File:** `nuc/intake/t0927s2/SWAP-T0927s2.sh`
- **Last changed:** 2026-11-21 · **Documented:** no

### `test_libraries.py`

T0927 slice 2 proof: libraries as config + required settings. Run INSIDE the intake image:

- **Run:** `docker run --rm -v DROP.toml:/config/drop.toml:ro --entrypoint python IMAGE test_libraries.py`
- **Where:** NUC · **File:** `nuc/intake/t0927s2/test_libraries.py`
- **Last changed:** 2026-11-21 · **Documented:** no

### `patch_t0927s3.py`

T0927 slice 3 in a COPY of intake: one virus gate and one placement step for every door.

- **Run:** `python3 patch_t0927s3.py ~/sandbox/t0927s3/intake`
- **Where:** NUC · **File:** `nuc/intake/t0927s3/patch_t0927s3.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_t0927s3.py`

proof that both doors share ONE virus gate and ONE placement step (T0927 slice 3), and that

- **Run:** `docker run --rm --entrypoint python IMAGE test_t0927s3.py`
- **Where:** NUC · **File:** `nuc/intake/t0927s3/test_t0927s3.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `t0932_step1_sbx.sh`

T0932 step 1 sandbox (2026-11-22, autopilot): Atomic Drop staging on the USB SSD.

- **Run:** `bash t0932_step1_sbx.sh`
- **Where:** NUC · **File:** `nuc/intake/t0932_step1_sbx.sh`
- **Last changed:** 2026-11-22 · **Documented:** no

### `patch_t0946_0.py`

T0946 item 0 in a COPY of classify.py: the sorter's instructions and the untrusted text are

- **Run:** `python3 patch_t0946_0.py ~/sandbox/t0946/intake`
- **Where:** NUC · **File:** `nuc/intake/t0946/patch_t0946_0.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_t0946_0.py`

proof for T0946 item 0. Run INSIDE the patched intake image (the model is faked):

- **Run:** `docker run --rm -e LLM_BASE=http://x --entrypoint python IMAGE test_t0946_0.py`
- **Where:** NUC · **File:** `nuc/intake/t0946/test_t0946_0.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `patch_t0958.py`

T0958, in a COPY of intake only: read the text in images (OCR) before the sorter decides.

- **Run:** `python3 patch_t0958.py ~/sandbox/t0958/intake`
- **Where:** NUC · **File:** `nuc/intake/t0958/patch_t0958.py`
- **Last changed:** 2026-11-21 · **Documented:** no

### `SWAP-T0958.sh`

put T0958 live: the drop reads the text in images (Tesseract) before the sorter decides.

- **Run:** `bash ~/sandbox/t0958/SWAP-T0958.sh`
- **Where:** NUC · **File:** `nuc/intake/t0958/SWAP-T0958.sh`
- **Last changed:** 2026-11-21 · **Documented:** no

### `test_t0958.py`

proof for T0958 (OCR before sorting). Run INSIDE the patched intake image (it has tesseract):

- **Run:** `docker run --rm -v DROP.toml:/config/drop.toml:ro -e PENDING_ROOT=/srv/unsorted/.pending \`
- **Where:** NUC · **File:** `nuc/intake/t0958/test_t0958.py`
- **Last changed:** 2026-11-21 · **Documented:** no

### `patch_t0962.py`

T0962 points 1 and 3, in a COPY of intake only.

- **Run:** `python3 patch_t0962.py ~/sandbox/t0962/intake`
- **Where:** NUC · **File:** `nuc/intake/t0962/patch_t0962.py`
- **Last changed:** 2026-11-21 · **Documented:** no

### `rescan_held.py`

T0962 point 4: re-check uploads that were held only because the virus scanner was down.

- **Run:** `docker exec intake python rescan_held.py`
- **Where:** NUC · **File:** `nuc/intake/t0962/rescan_held.py`
- **Last changed:** 2026-11-21 · **Documented:** no

### `SWAP-T0962-rescan.sh`

put T0962 point 4 live: an hourly re-check of uploads held while the virus scanner was

- **Run:** `bash ~/sandbox/t0962/SWAP-T0962-rescan.sh`
- **Where:** NUC · **File:** `nuc/intake/t0962/SWAP-T0962-rescan.sh`
- **Last changed:** 2026-11-21 · **Documented:** no

### `SWAP-T0962.sh`

put T0962 points 1, 2 + 3 live: /intake (Telegram + mail) gets the ClamAV check route_file

- **Run:** `bash ~/sandbox/t0962/SWAP-T0962.sh`
- **Where:** NUC · **File:** `nuc/intake/t0962/SWAP-T0962.sh`
- **Last changed:** 2026-11-21 · **Documented:** no

### `test_rescan.py`

proof for rescan_held.py (T0962 point 4). Run INSIDE the intake image; the scanner is faked.

- **Run:** `docker run --rm --entrypoint python IMAGE test_rescan.py`
- **Where:** NUC · **File:** `nuc/intake/t0962/test_rescan.py`
- **Last changed:** 2026-11-21 · **Documented:** no

### `test_t0962.py`

proof for T0962 points 1 and 3. Run INSIDE the intake image (no clamd needed: the scanner's

- **Run:** `docker run --rm --entrypoint python IMAGE test_t0962.py`
- **Where:** NUC · **File:** `nuc/intake/t0962/test_t0962.py`
- **Last changed:** 2026-11-21 · **Documented:** no

### `patch_t0962b.py`

T0962 point 5 + the rescan follow-ups from the 11-21 doc review, in a COPY of intake.

- **Run:** `python3 patch_t0962b.py ~/sandbox/t0962b/intake`
- **Where:** NUC · **File:** `nuc/intake/t0962b/patch_t0962b.py`
- **Last changed:** 2026-11-21 · **Documented:** no

### `SWAP-T0962b.sh`

T0962 point 5 (quarantine notes keep the person id, not the email; Jason 11-21: name only)

- **Run:** `bash ~/sandbox/t0962b/SWAP-T0962b.sh`
- **Where:** NUC · **File:** `nuc/intake/t0962b/SWAP-T0962b.sh`
- **Last changed:** 2026-11-21 · **Documented:** no

### `test_t0962b.py`

proof for T0962 point 5 + the rescan follow-ups. Run INSIDE the patched intake image:

- **Run:** `docker run --rm --entrypoint python IMAGE test_t0962b.py`
- **Where:** NUC · **File:** `nuc/intake/t0962b/test_t0962b.py`
- **Last changed:** 2026-11-21 · **Documented:** no

### `patch_t0964a.py`

T0964 tidy-ups that do not touch classify.py (those wait for the T0958 swap), in a COPY.

- **Run:** `python3 patch_t0964a.py ~/sandbox/t0964/intake`
- **Where:** NUC · **File:** `nuc/intake/t0964/patch_t0964a.py`
- **Last changed:** 2026-11-21 · **Documented:** no

### `SWAP-T0964a.sh`

T0964 tidy-ups outside classify.py: the video card shows the real upload limit

- **Run:** `bash ~/sandbox/t0964/SWAP-T0964a.sh`
- **Where:** NUC · **File:** `nuc/intake/t0964/SWAP-T0964a.sh`
- **Last changed:** 2026-11-21 · **Documented:** no

### `patch_t0964b.py`

T0964 rest (classify.py and stale comments), in a COPY of intake only.

- **Run:** `python3 patch_t0964b.py ~/sandbox/t0964b/intake`
- **Where:** NUC · **File:** `nuc/intake/t0964b/patch_t0964b.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `SWAP-T0964b.sh`

T0964 rest: Word/LibreOffice text, .csv, the document note, tabs in OCR text, MAX_GB, stale

- **Run:** `bash ~/sandbox/t0964b/SWAP-T0964b.sh`
- **Where:** NUC · **File:** `nuc/intake/t0964b/SWAP-T0964b.sh`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_t0964b.py`

proof for the T0964 classify tidy-ups. Run INSIDE the patched intake image (model faked):

- **Run:** `docker run --rm -v DROP.toml:/config/drop.toml:ro -e PENDING_ROOT=/srv/unsorted/.pending \`
- **Where:** NUC · **File:** `nuc/intake/t0964b/test_t0964b.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_abyss_handoff.py`

Abyss step 5 on the Novashare side (2026-11-24): the Abyss gateway row + health check, and the

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/test_abyss_handoff.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_browser.py`

Browser test for T0926 (2026-11-19): real Chrome, phone size, against the NUC sandbox via an ssh tunnel.

- **Run:** `python browser_test.py OUT_DIR`
- **Where:** NUC · **File:** `nuc/intake/test_browser.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `test_dropconfig.py`

T0927 slice 1 proof: the config file gives EXACTLY the old tables, and bad files fail closed.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/test_dropconfig.py`
- **Last changed:** 2026-11-21 · **Documented:** yes

### `test_finish.py`

sandbox test for T0925 (A4 finish retry, A10 '..' name, A11 disk reserve), 2026-11-19.

- **Run:** `python3 test_finish.py BASE RESERVE_BASE DATA_DIR`
- **Where:** NUC · **File:** `nuc/intake/test_finish.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `test_gatekeeper.py`

sandbox test for T0924 (A1 peer allowlist, A7 view-only, A9 video type), 2026-11-19.

- **Run:** `python3 test_gatekeeper.py BASE_ALLOWED BASE_BLOCKED`
- **Where:** NUC · **File:** `nuc/intake/test_gatekeeper.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `test_hzicon.py`

run INSIDE the patched intake image with a drop.toml mounted; THEME env says what to expect.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/test_hzicon.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `test_libraries.py`

T0927 slice 2 proof: libraries as config + required settings. Run INSIDE the intake image:

- **Run:** `docker run --rm -v DROP.toml:/config/drop.toml:ro --entrypoint python IMAGE test_libraries.py`
- **Where:** NUC · **File:** `nuc/intake/test_libraries.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `test_link_probe.py`

Tests for link_probe.py (T0889 step 3). Offline: DNS and the web are faked.

- **Run:** `python3 test_link_probe.py`
- **Where:** NUC · **File:** `nuc/intake/test_link_probe.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_link_route.py`

Tests for link_route.py (T0889 step 2): YouTube link detection, mp3/mp4 choice, MeTube body, send.

- **Run:** `python3 test_link_route.py`
- **Where:** NUC · **File:** `nuc/intake/test_link_route.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_parallel.py`

sandbox test for T0928 (numbered pieces in any order, several at once), 2026-11-19.

- **Run:** `python3 test_parallel.py BASE KEY DATA_DIR`
- **Where:** NUC · **File:** `nuc/intake/test_parallel.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `test_part2.py`

sandbox test for T0925 part 2 (A3 no freeze, shared key, A12 robin shelf, A14 health), 2026-11-19.

- **Run:** `python3 test_part2.py BASE KEY DATA_DIR RO_BASE`
- **Where:** NUC · **File:** `nuc/intake/test_part2.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `test_readable.py`

T0889 step 5: readable.extract keeps the article and drops menus, scripts, banners and attributes.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/test_readable.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_rescan.py`

proof for rescan_held.py (T0962 point 4). Run INSIDE the intake image; the scanner is faked.

- **Run:** `docker run --rm --entrypoint python IMAGE test_rescan.py`
- **Where:** NUC · **File:** `nuc/intake/test_rescan.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `test_resumable.py`

sandbox test for resumable.py (T0884, 2026-11-18). Talks to the SANDBOX intake

- **Run:** `python3 test_resumable.py [BASE] [DATA_DIR]`
- **Where:** NUC · **File:** `nuc/intake/test_resumable.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `test_scan.sh`

T0920 sandbox test: intake (patched copy) + clamav on a private docker network; intake streams

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/test_scan.sh`
- **Last changed:** 2026-11-21 · **Documented:** yes

### `test_t0889_share.py`

T0889 step 2: POST /share with a YouTube link goes to MeTube when METUBE_URL is set, else to the PDF path.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/test_t0889_share.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_t0889_step4.py`

T0889 step 4: direct file links (pdf / docx / images) are downloaded and filed; pages still go to PDF.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/test_t0889_step4.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_t0889_step6.py`

T0889 step 6 (2026-11-22): the Drop page's link card (portal.page()).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/test_t0889_step6.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `test_t0927s3.py`

proof that both doors share ONE virus gate and ONE placement step (T0927 slice 3), and that

- **Run:** `docker run --rm --entrypoint python IMAGE test_t0927s3.py`
- **Where:** NUC · **File:** `nuc/intake/test_t0927s3.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `test_t0931.py`

sandbox test for the 2026-11-20 re-audit fixes (T0931 must items), never the live intake.

- **Run:** `python3 test_t0931.py BASE KEY DATA_DIR CONTAINER`
- **Where:** NUC · **File:** `nuc/intake/test_t0931.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `test_t0931b.py`

in-process test of the T0931 review fixes (items 7-17, 23), 2026-11-20.

- **Run:** `python3 test_t0931b.py`
- **Where:** NUC · **File:** `nuc/intake/test_t0931b.py`
- **Last changed:** 2026-11-21 · **Documented:** yes

### `test_t0946_0.py`

proof for T0946 item 0. Run INSIDE the patched intake image (the model is faked):

- **Run:** `docker run --rm -e LLM_BASE=http://x --entrypoint python IMAGE test_t0946_0.py`
- **Where:** NUC · **File:** `nuc/intake/test_t0946_0.py`
- **Last changed:** 2026-11-22 · **Documented:** yes

### `test_t0958.py`

proof for T0958 (OCR before sorting). Run INSIDE the patched intake image (it has tesseract):

- **Run:** `docker run --rm -v DROP.toml:/config/drop.toml:ro -e PENDING_ROOT=/srv/unsorted/.pending \`
- **Where:** NUC · **File:** `nuc/intake/test_t0958.py`
- **Last changed:** 2026-11-22 · **Documented:** yes

### `test_t0962.py`

proof for T0962 points 1 and 3. Run INSIDE the intake image (no clamd needed: the scanner's

- **Run:** `docker run --rm --entrypoint python IMAGE test_t0962.py`
- **Where:** NUC · **File:** `nuc/intake/test_t0962.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `test_t0962b.py`

proof for T0962 point 5 + the rescan follow-ups. Run INSIDE the patched intake image:

- **Run:** `docker run --rm --entrypoint python IMAGE test_t0962b.py`
- **Where:** NUC · **File:** `nuc/intake/test_t0962b.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `test_t0964b.py`

proof for the T0964 classify tidy-ups. Run INSIDE the patched intake image (model faked):

- **Run:** `docker run --rm -v DROP.toml:/config/drop.toml:ro -e PENDING_ROOT=/srv/unsorted/.pending \`
- **Where:** NUC · **File:** `nuc/intake/test_t0964b.py`
- **Last changed:** 2026-11-22 · **Documented:** yes

### `test_themes.py`

T0942 step 1 proof. Run INSIDE the intake image (it needs fastapi):

- **Run:** `python test_themes.py dump`
- **Where:** NUC · **File:** `nuc/intake/test_themes.py`
- **Last changed:** 2026-11-22 · **Documented:** yes

### `test_ui.py`

proof for patch_ui.py (screens review 2026-11-23). Run INSIDE the patched image with a horizon

- **Run:** `docker run --rm -v nova.toml:/config/drop.toml:ro --entrypoint python IMAGE test_ui.py [DUMP_DIR]`
- **Where:** NUC · **File:** `nuc/intake/test_ui.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `build_horizon.sh`

sandbox build of the "horizon" theme (2026-11-22): a fresh copy of the live intake, the

- **Run:** `bash ~/sandbox/horizon/build_horizon.sh`
- **Where:** NUC · **File:** `nuc/intake/themes-horizon/build_horizon.sh`
- **Last changed:** 2026-11-22 · **Documented:** no

### `patch_hzfull.py`

fix: an empty /sending page showed the finished black hole + the inward ring (2026-11-22).

- **Run:** `python3 patch_hzfull.py FILE [FILE ...]`
- **Where:** NUC · **File:** `nuc/intake/themes-horizon/patch_hzfull.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `patch_hzicon.py`

give the horizon theme (Novashare) its own app icon, in a COPY of intake (2026-11-23).

- **Run:** `python3 patch_hzicon.py COPY_OF_INTAKE`
- **Where:** NUC · **File:** `nuc/intake/themes-horizon/patch_hzicon.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `shots_horizon.py`

the horizon theme at several progress values, forward and in reverse (2026-11-22).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/themes-horizon/shots_horizon.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `SWAP-NOVASHARE.sh`

Jason 2026-11-22: rename Atomic Share to "Novashare" with the collapsing-star animation

- **Run:** `bash ~/sandbox/horizon/SWAP-NOVASHARE.sh`
- **Where:** NUC · **File:** `nuc/intake/themes-horizon/SWAP-NOVASHARE.sh`
- **Last changed:** 2026-11-22 · **Documented:** no

### `gen_icons_garden.py`

the Garden Drop app icon (T0942 step 3, 2026-11-21): a grown plant in flower.

- **Run:** `python gen_icons_garden.py icons_png_garden.py`
- **Where:** NUC · **File:** `nuc/intake/themes-t0942/gen_icons_garden.py`
- **Last changed:** 2026-11-21 · **Documented:** yes

### `icons_png_garden.py`

the Garden Drop icon (SVG + PNG), made by gen_icons_garden.py (T0942). Do not edit.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/themes-t0942/icons_png_garden.py`
- **Last changed:** 2026-11-21 · **Documented:** yes

### `patch_theme.py`

T0942 step 1, in a COPY of intake only: make the drop's look come from themes.py.

- **Run:** `python3 patch_theme.py ~/sandbox/t0942/intake THEMES_GARDEN_PART`
- **Where:** NUC · **File:** `nuc/intake/themes-t0942/patch_theme.py`
- **Last changed:** 2026-11-21 · **Documented:** yes

### `shots.py`

phone-size screenshots of the drop's sending page in both themes (T0942). Run on the PC with an ssh

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/themes-t0942/shots.py`
- **Last changed:** 2026-11-21 · **Documented:** yes

### `shots_grow.py`

the garden plant at several progress values, forward and in reverse (T0942).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/themes-t0942/shots_grow.py`
- **Last changed:** 2026-11-21 · **Documented:** yes

### `test_themes.py`

T0942 step 1 proof. Run INSIDE the intake image (it needs fastapi):

- **Run:** `python test_themes.py dump`
- **Where:** NUC · **File:** `nuc/intake/themes-t0942/test_themes.py`
- **Last changed:** 2026-11-21 · **Documented:** yes

### `themes.py`

the drop's look, chosen by drop.toml [brand] theme (T0942, 2026-11-21).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/themes.py`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `patch_torrent.py`

Novashare -> qBittorrent route (magnet links, .torrent files), 2026-11-23, on a COPY of intake.

- **Run:** `python3 patch_torrent.py COPY_OF_INTAKE`
- **Where:** NUC · **File:** `nuc/intake/torrent-2026-11-23/patch_torrent.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `patch_torrent2.py`

torrent.py login check for qBittorrent 5.x (2026-11-23), on a COPY of intake.

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/torrent-2026-11-23/patch_torrent2.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `test_torrent.py`

proof for torrent.py against a FAKE qBittorrent (in this process). Run INSIDE the patched image:

- **Run:** `docker run --rm --entrypoint python IMAGE test_torrent.py`
- **Where:** NUC · **File:** `nuc/intake/torrent-2026-11-23/test_torrent.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `torrent.py`

send shared magnet links and .torrent files to qBittorrent (2026-11-23).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `nuc/intake/torrent-2026-11-23/torrent.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `app.py`

The collage: photographs of a wall, in the order they hang on it.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/collage/app.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `fixed_corner.py`

Clean the Gemini sparkle where the DETECTOR cannot see it, using position.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/collage/fixed_corner.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `unwatermark.py`

Take the Gemini sparkle off a whole folder, in place.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/collage/unwatermark.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `watermark.py`

Find Gemini's sparkle and paint it out.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/collage/watermark.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `app.py`

The DJ book: nights worked, and the photographs from them.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/dj/app.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `__init__.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/files/app/__init__.py`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `main.py`

/files — everyone sees what is theirs, and can take it away.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/files/app/main.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `build.py`

=============================================================================

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/build.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `collage_replace.py`

Rebuild the drifting mosaic behind Pee-Wee's name.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/collage_replace.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `connections.py`

=============================================================================

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/connections.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `handoffs.py`

=============================================================================

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/handoffs.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `intro_fx.py`

The cold open: an atom, then the supernova, then the wave going out.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/intro_fx.py`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `intros.py`

=============================================================================

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/intros.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `make_insignia.py`

Render the insignia on its own, for any project.

- **Run:** `python make_insignia.py`
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/make_insignia.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `make_video.py`

The Pee-Wee story, exactly as the site tells it, as one MP4.

- **Run:** `python make_video.py`
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/make_video.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `mapdata.py`

=============================================================================

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/mapdata.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `mapfilm.py`

The map act: Albany under a camera that flies where the story goes.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/mapfilm.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `mapregion.py`

=============================================================================

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/mapregion.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `narrate.py`

=============================================================================

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/narrate.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `patter.py`

=============================================================================

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/patter.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `portrait_act.py`

The page's own opening: his face, and the three lines under it.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/portrait_act.py`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `resize_all.py`

=============================================================================

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/resize_all.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `showcase.py`

=============================================================================

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/showcase.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `songscrape.py`

=============================================================================

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/songscrape.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `app.py`

=============================================================================

- **Run:** `streamlit run "[path] New Site\www\peewee\studio\app.py"`
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/studio/app.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `suggest.py`

=============================================================================

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/suggest.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `wall_replace.py`

Put a folder of photographs on Pee-Wee's wall, replacing what is there.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/peewee-tools/wall_replace.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `app.py`

/pi -- compute pi on SuperNova and keep only the equation and its answers (2026-11-08 / 11-09).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/pi-digits/app.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `snpi.py`

pi.snpi version 2: the stored result is an EQUATION plus its answers, nothing else

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/pi-digits/snpi.py`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `verify_pi.py`

check SuperNova's pi.snpi yourself. Nothing here trusts SuperNova.

- **Run:** `python3 verify_pi.py pi.snpi`
- **Where:** PC · **File:** `pc/ai-stack/pi-digits/verify_pi.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `install-task.ps1`

RUN ONCE AS ADMINISTRATOR. Registers the scheduled task "Pi y-cruncher" that runs

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/pi-digits/ycruncher/install-task.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `ycrun.ps1`

runs y-cruncher ELEVATED for /pi (Jason, 2026-11-08: "use both C and D and other drives ... go as many digits as possible").

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/pi-digits/ycruncher/ycrun.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `app.py`

The print queue watcher, talking to CUPS instead of Windows.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/printq/app.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `printserver.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/printserver.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `append_tsv.py`

Append new translations to lang/ko.tsv without disturbing existing lines.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/append_tsv.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `clean7.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/clean7.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `cmp_md5.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/cmp_md5.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `find_download.sh`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/find_download.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `fixsc.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/fixsc.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `g13.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/g13.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `g14.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/g14.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `g19.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/g19.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `gch2.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/gch2.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `gch3.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/gch3.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `gmail.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/gmail.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `grm.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/grm.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `h2t.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/h2t.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `helpers.sh`

---------- language: English, Korean, or both -----------------------------------

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/helpers.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `howsearch.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/howsearch.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `html_fix.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/html_fix.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `apply.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/jgp/apply.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `verify.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/jgp/verify.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `linkcheck.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/linkcheck.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `menu.sh`

---------- the menu -------------------------------------------------------------------

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/menu.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `merge14.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/merge14.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `add_spec.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/mine/add_spec.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `ai_tabs_spec.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/mine/ai_tabs_spec.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `form_spec.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/mine/form_spec.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `pipeline_spec.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/mine/pipeline_spec.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `ref.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/mine/ref.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `wl_spec.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/mine/wl_spec.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `peek2.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/peek2.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `pii.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/pii.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `plan_email.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/plan_email.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `ps1.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/ps1.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `renum.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/renum.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `router.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/router.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `rows.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/rows.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `rows2.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/rows2.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `ins.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/sp/ins.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `stop-wiki.sh`

Test-only: stop the wiki page import and wait for the install to finish.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/stop-wiki.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `subnet-check.sh`

Compare the old and the new way of finding the home network.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/subnet-check.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `t11.sh`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/t11.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `t6.sh`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/t6.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `to_author.py`

'the previous owner' -> 'the author' (Korean 이전 주인 -> 제작자, with the right particles).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/to_author.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `verify.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/verify.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `wgi.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/wgi.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `login_check.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/wikitest/login_check.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `verify.py`

Test helper: checks the test wiki at http://127.0.0.1:13000 against the seed.

- **Run:** `python verify.py SEED.json.gz ADMIN_PW_FILE`
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/wikitest/verify.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `wire.py`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/wire.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `wsl-prep.sh`

Test-only preparation of the WSL "junstest" distro to act like Robin's laptop.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/wsl-prep.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `wsl-prep2.sh`

Test-only preparation of the WSL "junstest" distro to act like Robin's laptop.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/wsl-prep2.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `places_import.py`

Add places from Google Maps.

- **Run:** `python places_import.py takeout`
- **Where:** PC · **File:** `pc/ai-stack/scripts/places_import.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `publish-ipa.sh`

Publish an iOS build from Codemagic, and read its version out of the file.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scripts/publish-ipa.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `release-app.sh`

Build Cut The Fluff, publish it, and prove what is being served.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scripts/release-app.sh`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `seed-plant-pages.py`

Build the backlog of Robin's wiki: a page for every plant he grows.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scripts/seed-plant-pages.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `seed-wiki-nav.py`

Give Robin's wiki a sidebar, so the pages are findable by browsing.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scripts/seed-wiki-nav.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `wikijs.py`

Talk to Wiki.js over its GraphQL API.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scripts/wikijs.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `app.py`

The console — one tab per thing you run, all in one page.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/tables/app.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `store.py`

Reading and writing the JSON files in Admin\data.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/tables/store.py`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `targets.py`

What each data file is, and what shape it has.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/tables/targets.py`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `websites.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/websites.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `site_smoke.ps1`

hit every site host and main-site path, record the HTTP status (2026-11-13, Claude).

- **Run:** `powershell -File site_smoke.ps1`
- **Where:** PC · **File:** `tools/site_smoke.ps1`
- **Last changed:** 2026-11-16 · **Documented:** no

### `stirling_31_patch.py`

move Stirling-PDF to 3.1.0 with household SSO (2026-11-18).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/stirling_31_patch.py`
- **Last changed:** 2026-11-19 · **Documented:** no

---

## 07 Developer Tools

*The tools that look after the code itself: indexes, checks, the public copy, and the GitHub and Dependabot helpers that keep every repo current. Run these when you change code, not when the site breaks.*

### `add_ps_comments.py`

Insert plain-English comments above PowerShell functions that have none (comment-only).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/add_ps_comments.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `test-pipefail.sh`

Does "long output | grep -q" give a wrong "no" under pipefail? (garden.sh uses set -uo pipefail)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scratchpad/files/test-pipefail.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `build_code_inventory.py`

Write SCRATCHPAD.md: every code file in ai-stack, what it does, and whether it is used.

- **Run:** `python -I scripts/build_code_inventory.py`
- **Where:** PC · **File:** `pc/ai-stack/scripts/build_code_inventory.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `doc_sidefx.py`

redo ONLY the "Side effects" section of docs that say "none" (2026-11-02).

- **Run:** `python scripts/doc_sidefx.py --list`
- **Where:** PC · **File:** `pc/ai-stack/scripts/doc_sidefx.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `docs_status.py`

where is the documentation backfill? (2026-10-31)

- **Run:** `python scripts/docs_status.py`
- **Where:** PC · **File:** `pc/ai-stack/scripts/docs_status.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `import_graph.py`

which Python file imports which other file in this repo (2026-11-01).

- **Run:** `python scripts/import_graph.py`
- **Where:** PC · **File:** `pc/ai-stack/scripts/import_graph.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `sync_scratchpad.py`

copy this PC's script folders to the NUC for the code scratchpad

- **Run:** `python -I scripts/sync_scratchpad.py`
- **Where:** PC · **File:** `pc/ai-stack/scripts/sync_scratchpad.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `archive_by_inventory.py`

move what SCRATCHPAD.md marks "outdated" / "one time use" out of ai-stack.

- **Run:** `python -I archive_by_inventory.py plan | apply | undo`
- **Where:** PC · **File:** `pc/archive-tools/archive_by_inventory.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `safe_unzip.py`

safe_unzip.py ZIP OUTDIR: extract a zip only if no entry escapes OUTDIR (absolute or ../ names).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/archive-tools/safe_unzip.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `tidy_ai_stack.py`

move backup copies, archives and zips out of the ai-stack repo to D:.

- **Run:** `python -I tidy_ai_stack.py plan | apply | undo`
- **Where:** PC · **File:** `pc/archive-tools/tidy_ai_stack.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `findings.py`

turn one lint or audit tool's output into the shared findings.json the NUC pulls (2026-11-17).

- **Run:** `python3 tools/ci/findings.py clippy`
- **Where:** PC · **File:** `tools/ci/findings.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `ci_scan_pin.py`

pin the ci_scan network subnet and trust both host gateways in gitea-proxy (T0630, 2026-11-18).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/ci_scan_pin.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `code_versions.py`

keep two months of compressed code versions on D: (2026-11-19).

- **Run:** `python code_versions.py`
- **Where:** PC · **File:** `tools/code_versions.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `daily_github_push.ps1`

once a night: commit the day's scratchpad changes, send them to Gitea, and push to

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/daily_github_push.ps1`
- **Last changed:** 2026-11-23 · **Documented:** no

### `daily_public_push.ps1`

twice a day: refresh the cleaned public copy from the Scratchpad, send it to Gitea,

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/daily_public_push.ps1`
- **Last changed:** 2026-11-18 · **Documented:** no

### `dependabot_midnight.py`

move every Dependabot schedule to 00:00 New York time (2026-11-19).

- **Run:** `python3 dependabot_midnight.py PATH/.github/dependabot.yml [--write]`
- **Where:** PC · **File:** `tools/dependabot_midnight.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `dependabot_one_pr.py`

rewrite a dependabot.yml so ALL version updates arrive as ONE monthly PR (2026-11-19).

- **Run:** `python3 dependabot_one_pr.py PATH/.github/dependabot.yml`
- **Where:** PC · **File:** `tools/dependabot_one_pr.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `doc_backfill_nuc.py`

first-draft docs for the NUC and Pi scripts in the Scratchpad, by the LOCAL model.

- **Run:** `py -3.14 -I [path] --list`
- **Where:** PC · **File:** `tools/doc_backfill_nuc.py`
- **Last changed:** 2026-11-21 · **Documented:** no

### `done_scan.sh`

scan ONLY the files of a finished project (T0630, Jason 2026-11-18).

- **Run:** `done_scan.sh <tag> <repo-dir> <out-dir>`
- **Where:** PC · **File:** `tools/done_scan.sh`
- **Last changed:** 2026-11-18 · **Documented:** no

### `github_fixes_in.py`

bring Dependabot fixes from the PUBLIC copy back into the PRIVATE Scratchpad (T0870).

- **Run:** `python tools/github_fixes_in.py`
- **Where:** PC · **File:** `tools/github_fixes_in.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `make_catalog.py`

write the Scratchpad's "magazine" catalog: CATALOG.md and catalog/index.html (2026-11-23).

- **Run:** `py -3.14 -I tools/make_catalog.py`
- **Where:** PC · **File:** `tools/make_catalog.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `make_tools_index.py`

write TOOLS.md: one plain line per script in the Scratchpad (2026-11-19).

- **Run:** `python tools/make_tools_index.py`
- **Where:** PC · **File:** `tools/make_tools_index.py`
- **Last changed:** 2026-11-20 · **Documented:** no

### `projects_public_copy.py`

copy the private Projects repo's CODE into scratchpad-public/projects/, cleaned.

- **Run:** `py -3.14 -I [path]`
- **Where:** PC · **File:** `tools/projects_public_copy.py`
- **Last changed:** 2026-11-20 · **Documented:** no

### `public_backport.py`

bring changes merged on the PUBLIC GitHub repo back into the private Scratchpad.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/public_backport.py`
- **Last changed:** 2026-11-20 · **Documented:** no

### `py_versions.py`

which Python every script and container runs on, and what blocks moving it up.

- **Run:** `py -3.14 -P py_versions.py`
- **Where:** PC · **File:** `tools/py_versions.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `scratch.py`

the scratchpad is the master copy of every script Jason runs his site with.

- **Run:** `python -I scratch.py import`
- **Where:** PC · **File:** `tools/scratch.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `site_repo_build.py`

build the one "homelab-site" repo from every machine's code (T0913, 2026-11-19).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/site_repo_build.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `start-dev.ps1`

start a work session: fresh pull, then VS Code, reachable from the phone (2026-11-19).

- **Run:** `powershell -ExecutionPolicy Bypass -File [path]`
- **Where:** PC · **File:** `tools/start-dev.ps1`
- **Last changed:** 2026-11-19 · **Documented:** no

### `start_check.ps1`

one PASS/FAIL check of everything a fresh session needs (Proton Pass on all three machines,

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/start_check.ps1`
- **Last changed:** 2026-11-18 · **Documented:** no

### `sync_main.sh`

bring `main` up to date in every clone on this PC, fast-forward only (2026-11-19).

- **Run:** `bash [path]`
- **Where:** PC · **File:** `tools/sync_main.sh`
- **Last changed:** 2026-11-19 · **Documented:** no

### `t0931_cycle.py`

one-day push/review/pull cycle for Atomic Drop (to-do T0931), 2026-11-20.

- **Run:** `py -3.14 -I -X utf8 [path] push`
- **Where:** PC · **File:** `tools/t0931_cycle.py`
- **Last changed:** 2026-11-20 · **Documented:** no

### `test_make_catalog.py`

checks for tools/make_catalog.py (stdlib only, 2026-11-23).

- **Run:** `py -3.14 -I tools/test_make_catalog.py`
- **Where:** PC · **File:** `tools/test_make_catalog.py`
- **Last changed:** 2026-11-24 · **Documented:** no

---

## 08 PC Care

*Keeping the Windows PC quiet, clean and up to date. Clean-up, updates, drive and health logs, the firewall, and the shortcuts on the desktop.*

### `autoclick.ps1`

dot-source this. Jason, 2026-11-04: "make sure it clicks yes too ... also the

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/autoclick.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `compact-docker-disk.ps1`

give Docker's freed space back to C: (2026-11-11).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/compact-docker-disk.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `finish-setup.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/finish-setup.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `fix-nuc-key.ps1`

run as administrator (2026-11-04). install-pc-maint.ps1 appended the NUC's restricted key onto

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/fix-nuc-key.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `games-off.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/games-off.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `gpu-rotation.ps1`

the GPU takes turns (Jason, 2026-11-05: "the backfill and book review/summary can go in 4-hour

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/gpu-rotation.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `install-pc-maint.ps1`

one-time, elevated (2026-11-04). Sets up the PC side of the NUC-led night maintenance.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/install-pc-maint.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `list-software.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/list-software.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pc-maint-remote.ps1`

the ONLY thing the NUC's SSH key may run on this PC (forced command in

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/pc-maint-remote.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pc-maintenance.ps1`

the PC's half of the night maintenance (Jason, 2026-11-04: "during the 3 am

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/pc-maintenance.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `quiet-services.ps1`

.SYNOPSIS

- **Run:** `.\quiet-services.ps1`
- **Where:** PC · **File:** `pc/pc-cleanup/quiet-services.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `remove-php-from-c-root.ps1`

finish removing PHP from [path] (2026-11-11).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/remove-php-from-c-root.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `restore-points.ps1`

Jason, 2026-11-04: "make one now and remove any before three [days] ago".

- **Run:** `.\restore-points.ps1`
- **Where:** PC · **File:** `pc/pc-cleanup/restore-points.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `round2.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/round2.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `run-admin-steps.ps1`

the three admin steps in one elevated window (2026-11-04). Output of each

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/run-admin-steps.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `run-uninstall-and-games.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/run-uninstall-and-games.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `schedule-finish-setup.ps1`

run ONCE, as administrator, right before you reboot:

- **Run:** `powershell -ExecutionPolicy Bypass -File "[path] New Site\www\pc-cleanup\schedule-finish-setup.ps1"`
- **Where:** PC · **File:** `pc/pc-cleanup/schedule-finish-setup.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `setup-gpu-rotation.ps1`

run ONCE in an ADMIN PowerShell (2026-11-05). Uses the steps already written in

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/setup-gpu-rotation.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `sort-files.ps1`

Jason's yes/no list, 2026-11-04. MOVES ONLY; nothing is deleted.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/sort-files.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `ssh-key-check.ps1`

read-only. Shows how Windows' SSH server reads administrators_authorized_keys

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/ssh-key-check.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `uninstall-the-rest.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/uninstall-the-rest.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `verify-uninstalls.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/verify-uninstalls.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `winget-update.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/pc-cleanup/winget-update.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pc-firewall-undo.ps1`

=============================================================================

- **Run:** `powershell -ExecutionPolicy Bypass -File "[path] New Site\www\pc-firewall-undo.ps1"`
- **Where:** PC · **File:** `pc/www-root/pc-firewall-undo.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pc-firewall.ps1`

=============================================================================

- **Run:** `powershell -ExecutionPolicy Bypass -File "[path] New Site\www\pc-firewall.ps1"`
- **Where:** PC · **File:** `pc/www-root/pc-firewall.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `after-reboot.ps1`

bring the /backlog feed and page back after a restart, then log the result.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/after-reboot.ps1`
- **Last changed:** 2026-11-16 · **Documented:** no

### `docker_trim.ps1`

back up, then remove Docker leftovers on the PC (2026-11-13, Claude).

- **Run:** `powershell -File docker_trim.ps1`
- **Where:** PC · **File:** `tools/docker_trim.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pc_drive_admin_setup.ps1`

the ONE admin step for drive SMART logging and fan speeds in Grafana (T0759, 2026-11-21).

- **Run:** `powershell -ExecutionPolicy Bypass -File [path]`
- **Where:** PC · **File:** `tools/pc_drive_admin_setup.ps1`
- **Last changed:** 2026-11-21 · **Documented:** no

### `pc_drive_health.ps1`

every 5 minutes, each drive's SMART counters, to find the click (T0759, 2026-11-21).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/pc_drive_health.ps1`
- **Last changed:** 2026-11-23 · **Documented:** no

### `pc_health_admin_setup.ps1`

the ONE admin step for fan and GPU metrics in Grafana (2026-11-17).

- **Run:** `powershell -ExecutionPolicy Bypass -File [path]`
- **Where:** PC · **File:** `tools/pc_health_admin_setup.ps1`
- **Last changed:** 2026-11-18 · **Documented:** no

### `pc_health_log.ps1`

one line a minute about the PC's fans, GPU, CPU and disks, kept 30 days, to find what

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/pc_health_log.ps1`
- **Last changed:** 2026-11-18 · **Documented:** no

---

## 09 Operations and Backups

*Keeping the machines running and the data safe. Backups and restore drills, the NAS, the Pi's monitors, start-up and repair scripts for the AI stack, and the nightly jobs.*

### `apps.ps1`

.SYNOPSIS

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/apps.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `autostart.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/autostart.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `certs.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/certs.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `certwatch.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/certwatch.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `db-export.ps1`

Export every database on SuperNova. Mondays and Thursdays, 07:00.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/db-export.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `diag.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/diag.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `docker-fix.ps1`

what the `fix` command runs (Jason, 2026-11-04: "type fix over SSH and it

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/docker-fix.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `files-backup.sh`

Back everyone's files up to the encrypted external SSD on the NUC.

- **Run:** `./files-backup.sh`
- **Where:** PC · **File:** `pc/ai-stack/files-backup.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `firewall.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/firewall.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `fix-run.ps1`

what typing `fix` does. Starts the "Docker Fix" scheduled task (install-fix.ps1

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/fix-run.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `gpu-check.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/gpu-check.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `hostmetrics.ps1`

The numbers only Windows can see.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/hostmetrics.ps1`
- **Last changed:** 2026-11-23 · **Documented:** yes

### `index-feed.ps1`

Ship the PC's searchable data to the NUC's index. Hourly.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/index-feed.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `install-fix.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/install-fix.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `install-shortcut.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/install-shortcut.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `app.py`

Record the machine into SQLite every two minutes. Keep one month. Hand it

- **Run:** `Docker API`
- **Where:** PC · **File:** `pc/ai-stack/metrics/app.py`
- **Last changed:** 2026-11-20 · **Documented:** yes

### `n8n-check.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/n8n-check.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `n8n-export.ps1`

=============================================================================

- **Run:** `.\n8n-export.ps1`
- **Where:** PC · **File:** `pc/ai-stack/n8n-export.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `kuma-add-hosts.py`

Add plain ping monitors for hosts. Run ON THE PI, as root, container STOPPED.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/kuma-add-hosts.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `kuma-add-power.py`

Add PUSH monitors that warn about under-voltage. Run ON THE PI, kuma STOPPED.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/kuma-add-power.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `kuma-add-services.py`

Monitor the services that went down unnoticed in the 2026-09-13 outage.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/kuma-add-services.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `kuma-fix-addresses-and-power.py`

Fix the NUC Kuma: correct the Pi 3's address, and move power alerting here.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/kuma-fix-addresses-and-power.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `kuma-notify.py`

Attach the ntfy channel to every Kuma monitor. Run ON THE PI, as root,

- **Run:** `docker stop uptime-kuma-pi`
- **Where:** Pi · **File:** `pc/ai-stack/pi/kuma-notify.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `kuma-nuc-hosts.py`

Add host monitors to the NUC's Kuma. Run ON THE NUC, container STOPPED.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/kuma-nuc-hosts.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `kuma-seed.py`

Seed the Pi's Uptime Kuma with the checks that would have caught tonight.

- **Run:** `sudo systemctl stop docker`
- **Where:** Pi · **File:** `pc/ai-stack/pi/kuma-seed.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `ntfy-users.sh`

Create the ntfy accounts. Run ON THE PI.

- **Run:** `./ntfy-users.sh`
- **Where:** Pi · **File:** `pc/ai-stack/pi/ntfy-users.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `offload-videos.sh`

Move finished downloads off the Pi and onto the NAS, and only then delete.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/offload-videos.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pi-power-watch-install.sh`

Install the power watcher and its timer. Run ON A PI.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/pi-power-watch-install.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pi-power-watch.sh`

Report this board's power health to Kuma. Run by a systemd timer.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/pi-power-watch.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pi-sdcard-guard.sh`

Cut what the Pi writes to its SD card, so an always-on node does not eat it.

- **Run:** `./pi-sdcard-guard.sh`
- **Where:** Pi · **File:** `pc/ai-stack/pi/pi-sdcard-guard.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pi-wipe.sh`

Wipe the Pi back to a clean compute/monitoring node -- without bricking it.

- **Run:** `sudo apt purge $(apt-mark showmanual) -y`
- **Where:** Pi · **File:** `pc/ai-stack/pi/pi-wipe.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pi3-bootstrap.sh`

Pi 3 "Tor" -- the one step that needs your password, so nothing else does.

- **Run:** `ssh -t jason@[lan-ip] 'bash -s' < pi3-bootstrap.sh`
- **Where:** Pi · **File:** `pc/ai-stack/pi/pi3-bootstrap.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pi3-lowpower.sh`

Cut the Pi 3's current draw so a marginal supply stops browning out.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/pi3-lowpower.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pi3-tor.sh`

Tor on the Pi 3, as a CLIENT + LAN SOCKS proxy. Never a relay, never an exit.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/pi3-tor.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pi3-usb-mount.sh`

Mount the USB stick NON-DESTRUCTIVELY and give the Pi a scratch area on it.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/pi3-usb-mount.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `pi5-ssd-downloads.sh`

Move the Pi 5's torrent storage from sshfs-over-the-network to a local SSD.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/pi5-ssd-downloads.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `playlist-match.py`

Turn a Spotify or YouTube playlist into a Navidrome playlist of YOUR files.

- **Run:** `python3 playlist-match.py --from https://open.spotify.com/playlist/37i9...`
- **Where:** Pi · **File:** `pc/ai-stack/pi/playlist-match.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `stack-ensure-install.sh`

Install stack-ensure as a boot-and-every-10-minutes systemd timer.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/stack-ensure-install.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `stack-ensure.sh`

Bring up anything in the Pi 5 stack that is not running.

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/stack-ensure.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `tang-bind.sh`

Bind the NUC's LUKS vault to the Tang server on the Pi. RUN ON THE NUC.

- **Run:** `sudo ./tang-bind.sh`
- **Where:** Pi · **File:** `pc/ai-stack/pi/tang-bind.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `usb-disk-setup.sh`

Format and mount a USB SSD for the download lane. Run ON THE PI.

- **Run:** `./usb-disk-setup.sh`
- **Where:** Pi · **File:** `pc/ai-stack/pi/usb-disk-setup.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `vpn-killswitch-test.sh`

PROVE the kill switch. Not "the firewall rules look right" -- actually

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pc/ai-stack/pi/vpn-killswitch-test.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `preflight.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/preflight.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `restart-docker.ps1`

unfreeze Docker Desktop and keep the GPU free.

- **Run:** `right-click > Run with PowerShell`
- **Where:** PC · **File:** `pc/ai-stack/restart-docker.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `routes.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/routes.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `rust-setup.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/rust-setup.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `drive-sync.sh`

Pull Google Drive down to this box, on a timer.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/scripts/drive-sync.sh`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `Update-Stack.ps1`

.SYNOPSIS

- **Run:** `.\Update-Stack.ps1`
- **Where:** PC · **File:** `pc/ai-stack/scripts/Update-Stack.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `setup.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/setup.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `update.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/update.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `vector.ps1`

=============================================================================

- **Run:** `.\vector.ps1 -Install`
- **Where:** PC · **File:** `pc/ai-stack/vector.ps1`
- **Last changed:** 2026-11-16 · **Documented:** yes

### `verify.ps1`

(no header comment yet)

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/verify.ps1`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `watch.py`

Watch the other machine, and say so on Telegram when it stops answering.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `pc/ai-stack/watchdog/watch.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `pi_controld.py`

Control D as the DNS of the Pi's VPN stack (2026-11-23). Run on the Pi in ~/stack.

- **Run:** `python3 pi_controld.py IMAGE_WITH_DIGEST`
- **Where:** Pi · **File:** `pi/pi_controld.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `pi_downloads_to_nas.py`

move the Pi's download lane onto the NAS Downloads share (2026-11-23, T0082).

- **Run:** `python3 ~/stack/pi_downloads_to_nas.py`
- **Where:** Pi · **File:** `pi/pi_downloads_to_nas.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `qbt_fix.py`

give the Pi's qBittorrent a permanent WebUI password kept in Proton Pass (T0872, 2026-11-18).

- **Run:** no usage line in the header
- **Where:** Pi · **File:** `pi/tools/qbt_fix.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `break_finder.py`

Break-finder (T1012): list every file or folder this machine's jobs depend on, and say which are missing.

- **Run:** `python3 break_finder.py [--out FILE] [--no-scripts]`
- **Where:** PC · **File:** `tools/break_finder.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `code_backup.py`

back up ALL code to the NAS (2026-11-23, Jason: "Backup the entire code base to the nas all of it").

- **Run:** `py -3.14 -I [path]`
- **Where:** PC · **File:** `tools/code_backup.py`
- **Last changed:** 2026-11-23 · **Documented:** no

### `config_export_pull.py`

pull the NUC's secret-free config export into the Scratchpad and commit it.

- **Run:** `py -3.14 -I [path]`
- **Where:** PC · **File:** `tools/config_export_pull.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `Backup-ToNas.ps1`

.SYNOPSIS

- **Run:** `.\Backup-ToNas.ps1`
- **Where:** NAS · **File:** `tools/copy/Backup-ToNas.ps1`
- **Last changed:** 2026-11-16 · **Documented:** no

### `Copy-Verified.ps1`

.SYNOPSIS

- **Run:** `.\Copy-Verified.ps1 '[path] for all' '[share] backup\Garden-for-all'`
- **Where:** PC · **File:** `tools/copy/Copy-Verified.ps1`
- **Last changed:** 2026-11-16 · **Documented:** no

### `Install-SendTo.ps1`

.SYNOPSIS

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/copy/Install-SendTo.ps1`
- **Last changed:** 2026-11-16 · **Documented:** no

### `Register-CopyTasks.ps1`

.SYNOPSIS

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/copy/Register-CopyTasks.ps1`
- **Last changed:** 2026-11-16 · **Documented:** no

### `SendTo-TeraCopy.ps1`

.SYNOPSIS

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/copy/SendTo-TeraCopy.ps1`
- **Last changed:** 2026-11-16 · **Documented:** no

### `Set-TeraCopyReports.ps1`

.SYNOPSIS

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/copy/Set-TeraCopyReports.ps1`
- **Last changed:** 2026-11-16 · **Documented:** no

### `nas_dsm_prom.py`

the Synology NAS's disks, SMART, storage pool (RAID) and SSD cache state for Prometheus.

- **Run:** `python3 ~/hwmon/nas_dsm_prom.py --print`
- **Where:** NAS · **File:** `tools/hwmon/nas_dsm_prom.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `kopia_add_d_sources.ps1`

add the [path] folders that were in no backup to the PC's KopiaUI repo

- **Run:** `powershell -NoProfile -ExecutionPolicy Bypass -File [path] [-NoSnapshot]`
- **Where:** PC · **File:** `tools/kopia_add_d_sources.ps1`
- **Last changed:** 2026-11-18 · **Documented:** no

### `nas_move.py`

T1014 phase 2: carry out a move list ON the Synology NAS (Python 3.8), journaled, restartable.

- **Run:** `python3 ~/t1014/nas_move.py OPS.jsonl JOURNAL.jsonl [--minutes 15]`
- **Where:** NAS · **File:** `tools/nas_move.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `nas_restore.py`

T1014: undo nas_move.py. Puts every journaled file back where it was. Run ON the NAS.

- **Run:** `python3 ~/t1014/nas_restore.py JOURNAL.jsonl [JOURNAL2.jsonl ...] [--dry]`
- **Where:** NAS · **File:** `tools/nas_restore.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `nas_sha.py`

T1014: full SHA-256 of a list of files, run ON the Synology NAS (Python 3.8). Read-only.

- **Run:** `nohup python3 ~/t1014/nas_sha.py ~/t1014/list.jsonl ~/t1014/sha.jsonl > ~/t1014/sha.log 2>&1 &`
- **Where:** NAS · **File:** `tools/nas_sha.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `nas_verify.py`

T1014 phase 2 check, run ON the NAS (Python 3.8). Read-only.

- **Run:** `python3 ~/t1014/nas_verify.py`
- **Where:** NAS · **File:** `tools/nas_verify.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `patch_t0052d_20261120.py`

patch_t0052d.py - T0052 step d / T0229 (2026-11-20 autopilot): one retry on Actual's bank sync,

- **Run:** `python3 patch_t0052d.py ~/money-autopilot/money_daily.mjs`
- **Where:** NUC · **File:** `tools/nuc/patch_t0052d_20261120.py`
- **Last changed:** 2026-11-20 · **Documented:** no

### `test_t0052d.sh`

T0052d / T0229 retry block, tested with a stub api (no Actual, no live state.json).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `tools/nuc/test_t0052d.sh`
- **Last changed:** 2026-11-20 · **Documented:** no

### `nas_check.py`

re-hash files ON the NAS and compare with expected SHA-256 values. Read-only.

- **Run:** `python3 nas_check.py LIST.jsonl`
- **Where:** NAS · **File:** `tools/photos_syno/nas_check.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `nas_dedupe.py`

plan step 4: delete exact duplicates (same SHA-256) in the photo library. Runs ON the NAS.

- **Run:** `python3 nas_dedupe.py plan`
- **Where:** NAS · **File:** `tools/photos_syno/nas_dedupe.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `nas_index.py`

SHA-256 index of the photo areas, run ON the Synology NAS (Python 3.8). Read-only on the photos.

- **Run:** `nohup python3 ~/photos_syno/nas_index.py ~/photos_syno/index.jsonl > ~/photos_syno/index.log 2>&1 &`
- **Where:** NAS · **File:** `tools/photos_syno/nas_index.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `nas_move.py`

plan step 3: move the photo library from the Pictures share into Jason's personal space.

- **Run:** `python3 nas_move.py plan`
- **Where:** NAS · **File:** `tools/photos_syno/nas_move.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `nas_takeout.py`

Google Takeout check (Jason 11-24): are the Takeout photos duplicates of the library?

- **Run:** `python3 nas_takeout.py report INDEX.jsonl OPS.jsonl REPORT.txt`
- **Where:** NAS · **File:** `tools/photos_syno/nas_takeout.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `nas_api_defs.py`

READ-ONLY: print the DSM web API definitions (methods/versions) for the snapshot and

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/security/nas_api_defs.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `nas_sched_read.py`

READ-ONLY: show the existing snapshot schedule/retention of one share (default

- **Run:** `python [path] ["share name" ...]`
- **Where:** PC · **File:** `tools/security/nas_sched_read.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `nas_security_probe.py`

READ-ONLY check of the NAS before scheduling snapshots and AV (2026-11-24).

- **Run:** `python [path]`
- **Where:** PC · **File:** `tools/security/nas_security_probe.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `nas_snap_av_schedule.py`

NAS hardening, approved 2026-11-24 (item 2): hourly Btrfs share snapshots with

- **Run:** `python -I [path]`
- **Where:** PC · **File:** `tools/security/nas_snap_av_schedule.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `nas_ssh_probe.py`

READ-ONLY NAS probe over SSH (root via photos_syno/nas.py script()): volumes, shares,

- **Run:** `python [path]`
- **Where:** PC · **File:** `tools/security/nas_ssh_probe.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `test_nas_move.py`

nas_move.py + nas_restore.py on temp folders (the rename path; the NAS reflink path was

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/test_nas_move.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `test_unify_plan.py`

checks unify_plan.build() on fake rows (no NAS needed). Run: python test_unify_plan.py

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/test_unify_plan.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `unified_backup.py`

one unified copy of the whole site (PC, NUC, Pi, Garden for All) on the NAS, never overwriting.

- **Run:** `py -3 -I [path] --dry`
- **Where:** NAS · **File:** `tools/unified_backup.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `unify_ops.py`

T1014 phase 2: turn unify-plan.csv into the NAS move list (ops.jsonl). Changes no file.

- **Run:** `python [path]`
- **Where:** PC · **File:** `tools/unify_ops.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `unify_photos.py`

T1014 phase 1 (READ-ONLY): inventory, hash and date every photo on P:, Z: and the M:-only files.

- **Run:** `python [path] all`
- **Where:** NAS · **File:** `tools/unify_photos.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `unify_plan.py`

T1014 phase 1: turn the cached inventory/hashes/dates into a DRY-RUN plan. Changes no photo.

- **Run:** `python [path] plan`
- **Where:** PC · **File:** `tools/unify_plan.py`
- **Last changed:** 2026-11-19 · **Documented:** no

---

## 10 Workshop

*Tools that fit no chapter: one-off projects, photo and media helpers, the to-do list, and experiments. Handy to know about, rarely needed.*

### `export_transcripts.py`

Export transcripts.

- **Run:** `python scripts/export_transcripts.py --list`
- **Where:** PC · **File:** `pc/ai-stack/scripts/export_transcripts.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `split_todo_note.py`

split the Proton Pass "To Do" note into numbered part notes (2026-10-31).

- **Run:** `python split_todo_note.py`
- **Where:** PC · **File:** `pc/ai-stack/scripts/split_todo_note.py`
- **Last changed:** 2026-11-19 · **Documented:** yes

### `aimode_pdf.py`

a Google AI Mode chat link -> ONE long PDF page (no page breaks), on the PC.

- **Run:** `python [path] https://share.google/aimode/XXXX`
- **Where:** PC · **File:** `tools/aimode_pdf.py`
- **Last changed:** 2026-11-20 · **Documented:** no

### `push-feed-changed.sh`

copy only the CHANGED files of the PC's feed/ to the NUC backlog page, over SSH.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/backlog/push-feed-changed.sh`
- **Last changed:** 2026-11-18 · **Documented:** no

### `broadview_batches.py`

Turn broadview.json (broadview_parse.py) into Actual import batches (2026-11-17, money import).

- **Run:** `python broadview_batches.py broadview.json OUTDIR SUFFIX FROM TO [SIZE]`
- **Where:** PC · **File:** `tools/broadview_batches.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `broadview_parse.py`

Parse Broadview FCU eStatement PDFs into transactions, and prove each month adds up (2026-11-17, money import).

- **Run:** `python broadview_parse.py OUT.json PDF [PDF ...]`
- **Where:** PC · **File:** `tools/broadview_parse.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `close_items_20261118.py`

Close finished to-do items: tick, add Done/Review lines, move the block to TODO-DONE.md (2026-11-18).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/close_items_20261118.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `dedupe_media.py`

set aside duplicate media files (2026-11-14, Claude, for Jason).

- **Run:** `py -3 -I dedupe_media.py`
- **Where:** NAS · **File:** `tools/dedupe_media.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `doc_compare_nuc.py`

Read-only: which NUC documents are missing from the documents master (T0936 step 1c/2, 2026-11-24).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/doc_compare_nuc.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `doc_stage_copy.py`

Copy the documents missing from the master into a STAGING folder (T0936 step 3, 2026-11-24).

- **Run:** `python doc_stage_copy.py <cmp_dir> <stage_dir> [--dry-run]`
- **Where:** PC · **File:** `tools/doc_stage_copy.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `env_copies_to_pass.py`

clean the leftover plain-text .env copies on the PC, with a rollback copy in Pass (2026-11-24).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/env_copies_to_pass.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `env_copies_verify.py`

check each file env_copies_to_pass.py rewrote against its Pass backup note (2026-11-24).

- **Run:** `py -3.14 -I [path]`
- **Where:** PC · **File:** `tools/env_copies_verify.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `folder_inventory.py`

read-only list of every top folder on this machine, one CSV row each (T0002 step 1).

- **Run:** `python3 folder_inventory.py MACHINE OUT.csv ROOT[:DEPTH] [ROOT[:DEPTH] ...]`
- **Where:** PC · **File:** `tools/folder_inventory.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `hw_pc_smart.ps1`

every drive's SMART health into Grafana and a CSV log, once a minute (2026-11-24).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/hwmon/hw_pc_smart.ps1`
- **Last changed:** 2026-11-24 · **Documented:** no

### `hw_watch.py`

every 5 minutes: is any fan, drive or NAS disk/pool/cache getting worse? (2026-11-24)

- **Run:** `python3 ~/hwmon/hw_watch.py`
- **Where:** PC · **File:** `tools/hwmon/hw_watch.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `hwmon_pc_admin_setup.ps1`

the ONE admin step for PC drive SMART monitoring (2026-11-24).

- **Run:** `powershell -NoProfile -ExecutionPolicy Bypass -File [path]`
- **Where:** PC · **File:** `tools/hwmon/hwmon_pc_admin_setup.ps1`
- **Last changed:** 2026-11-24 · **Documented:** no

### `hwmon_pc_fix_task_path.ps1`

move the SYSTEM task's script to an admin-only folder (audit 2026-11-24, High #2).

- **Run:** `powershell -NoProfile -ExecutionPolicy Bypass -File [path]`
- **Where:** PC · **File:** `tools/hwmon/hwmon_pc_fix_task_path.ps1`
- **Last changed:** 2026-11-24 · **Documented:** no

### `nuc_hwmon_root_setup.sh`

the ONE sudo step on the NUC for hardware-health monitoring (2026-11-24).

- **Run:** `sudo bash ~/hwmon/nuc_hwmon_root_setup.sh`
- **Where:** NUC · **File:** `tools/hwmon/nuc_hwmon_root_setup.sh`
- **Last changed:** 2026-11-24 · **Documented:** no

### `nuc_smart_prom.py`

the NUC's own drives' SMART health for Prometheus (2026-11-24).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/hwmon/nuc_smart_prom.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `manga.py`

the scratchpad as manga-style flip books, one PDF per function zone, for Kavita.

- **Run:** `python -I manga.py`
- **Where:** PC · **File:** `tools/manga.py`
- **Last changed:** 2026-11-20 · **Documented:** no

### `ngrams_fetch.sh`

T1018 (Jason 11-18: "do the recommended"): rebuild ~/languagetool-ngrams, lost in the 11-15 wipe.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/ngrams_fetch.sh`
- **Last changed:** 2026-11-18 · **Documented:** no

### `pass_find_items.py`

Find Proton Pass items by title match and report found / not found only.

- **Run:** `python pass_find_items.py --vault "NUC services" --pattern paperless --pattern jellyfin --field password`
- **Where:** PC · **File:** `tools/pass_find_items.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `pass_item_shape.py`

Print one Pass item's JSON SHAPE: keys, types, and string LENGTHS only.

- **Run:** `python -I pass_item_shape.py "NUC services" "JELLYFIN_API_KEY (2026-11-24)"`
- **Where:** PC · **File:** `tools/pass_item_shape.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `pc_admin_batch_2026-11-23.ps1`

the PC steps from 2026-11-23 that need an ADMIN PowerShell, in one run.

- **Run:** `powershell -NoProfile -ExecutionPolicy Bypass -File [path]`
- **Where:** PC · **File:** `tools/pc_admin_batch_2026-11-23.ps1`
- **Last changed:** 2026-11-23 · **Documented:** no

### `pc_env_to_pass.py`

move the last plain secrets in the PC's ai-stack .env into Proton Pass (2026-11-24).

- **Run:** `py -3.14 -I [path]`
- **Where:** PC · **File:** `tools/pc_env_to_pass.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `pc_images_to_nuc.ps1`

send the PC's running containers to the NUC for the Tuesday update email (2026-11-18).

- **Run:** no usage line in the header
- **Where:** NUC · **File:** `tools/pc_images_to_nuc.ps1`
- **Last changed:** 2026-11-18 · **Documented:** no

### `pc_noise_logger.ps1`

every 5 seconds: every fan's RPM, CPU/GPU temperature and load, and the two hard drives'

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/pc_noise_logger.ps1`
- **Last changed:** 2026-11-23 · **Documented:** no

### `pc_noise_logger_setup.ps1`

ONE TIME, as admin: install the 5-second noise logger as a SYSTEM task (2026-11-23).

- **Run:** `powershell -NoProfile -ExecutionPolicy Bypass -File [path]`
- **Where:** PC · **File:** `tools/pc_noise_logger_setup.ps1`
- **Last changed:** 2026-11-23 · **Documented:** no

### `pc_share_nucreader.ps1`

Requires -RunAsAdministrator

- **Run:** `powershell -NoProfile -ExecutionPolicy Bypass -File [path]`
- **Where:** PC · **File:** `tools/pc_share_nucreader.ps1`
- **Last changed:** 2026-11-24 · **Documented:** no

### `photo_meta.py`

small, dependency-free helpers for the T1014 photo unify plan (unify_photos.py).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/photo_meta.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `abyss_photos_setup.py`

read-only NAS photos for Abyss Find (nas-photos), Jason's "do the small fixes" 2026-11-24.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/photos_syno/abyss_photos_setup.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `abyss_ro_probe.py`

READ-ONLY look at the DSM APIs needed for the abyss-ro user (2026-11-24). Prints API

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/photos_syno/abyss_ro_probe.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `abyss_trim_probe.py`

READ-ONLY: DSM API versions + state for the abyss-trim user/share/task (2026-11-24).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/photos_syno/abyss_trim_probe.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `abyss_trim_probe2.py`

READ-ONLY: data formats of user homelab (groups, app rules, share perms) and script task 4.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/photos_syno/abyss_trim_probe2.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `abyss_trim_probe3.py`

READ-ONLY: find the AppPriv rule read format; user get format (names only).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/photos_syno/abyss_trim_probe3.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `abyss_trim_probe4.py`

READ-ONLY: which entities hold rules on each DSM app (names only).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/photos_syno/abyss_trim_probe4.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `abyss_trim_setup.py`

create the NAS side of the Abyss trimmed-clips drop (Jason approved 11-24).

- **Run:** `bash /volume1/homes/jay/photos_syno/abyss-trim-move.sh`
- **Where:** PC · **File:** `tools/photos_syno/abyss_trim_setup.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `creds.py`

read Jason's DSM login from Proton Pass into memory. Never prints a value or an item title.

- **Run:** `python creds.py --check`
- **Where:** PC · **File:** `tools/photos_syno/creds.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `dedupe_driver.py`

step 4 driver on the PC: delete in batches of 5,000 with sync + storage check + pause between.

- **Run:** `python [path] TOTAL [pause_seconds]`
- **Where:** PC · **File:** `tools/photos_syno/dedupe_driver.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `dsm_api.py`

minimal Synology DSM Web API client (login, call, logout). Never prints the sid or password.

- **Run:** `python dsm_api.py --check`
- **Where:** PC · **File:** `tools/photos_syno/dsm_api.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `dsm_web.py`

Dsm session like the DSM web UI (session "webui" + SynoToken header). Some DSM 7.2 writes

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/photos_syno/dsm_web.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `foto_status.py`

plan step 7: read-only check that Synology Photos sees the personal-space library.

- **Run:** `python [path]`
- **Where:** PC · **File:** `tools/photos_syno/foto_status.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `map_p.py`

plan step 6: map P: to [share] (Jason's personal-space photos), persistent.

- **Run:** `python [path]`
- **Where:** NAS · **File:** `tools/photos_syno/map_p.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `move_driver.py`

step 3 driver on the PC: one year folder per batch, storage check + pause between batches.

- **Run:** `python [path] [pause_seconds]`
- **Where:** PC · **File:** `tools/photos_syno/move_driver.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `nas.py`

run commands on the Synology NAS over SSH as Jason's DSM user, password from Pass via SSH_ASKPASS.

- **Run:** `python nas.py "command"`
- **Where:** NAS · **File:** `tools/photos_syno/nas.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `pc_fill.py`

plan step 5: copy PC photos/videos that are NOT on the NAS into Photos/Library/YYYY/MM.

- **Run:** `python pc_fill.py plan`
- **Where:** NAS · **File:** `tools/photos_syno/pc_fill.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `pc_scan.py`

list and SHA-256 every photo/video file on the PC drives C: D: G: M:. Read-only on the PC.

- **Run:** `python [path] list`
- **Where:** PC · **File:** `tools/photos_syno/pc_scan.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `photos_api_probe.py`

READ-ONLY: list the Synology Photos (SYNO.Foto*) APIs and their versions on the NAS,

- **Run:** no usage line in the header
- **Where:** NAS · **File:** `tools/photos_syno/photos_api_probe.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `storage_check.py`

between-batch NAS storage guard (coordinator 11-24: stop at once on any storage warning).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/photos_syno/storage_check.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `photos_to_nas.ps1`

copy every [path] file that has NO copy on the NAS into the NAS Pictures share,

- **Run:** no usage line in the header
- **Where:** NAS · **File:** `tools/photos_to_nas.ps1`
- **Last changed:** 2026-11-24 · **Documented:** no

### `prescan_tags.sh`

tag every not-yet-worked-on project for a Gitea pre-scan (T0630, Jason 2026-11-18:

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/prescan_tags.sh`
- **Last changed:** 2026-11-18 · **Documented:** no

### `raw_check_hold.py`

T0005: move the 57 'raw-check' RAWs that have a same-name JPG into the holding folder.

- **Run:** `python raw_check_hold.py list`
- **Where:** PC · **File:** `tools/raw_check_hold.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `raw_to_jpg.py`

T1014 phase 2: convert the RAW-only shots (action raw-convert) to JPG, keeping date + EXIF.

- **Run:** `python [path] [--limit N]`
- **Where:** PC · **File:** `tools/raw_to_jpg.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `rej_unclean.py`

apply public-copy .rej hunks to the private Scratchpad by undoing the placeholders first.

- **Run:** `py -3.14 -I -X utf8 [path] [--write] FILE.rej [...]`
- **Where:** PC · **File:** `tools/rej_unclean.py`
- **Last changed:** 2026-11-20 · **Documented:** no

### `remote-control-server.ps1`

keep Claude Code Remote Control running on the PC, always (2026-11-19).

- **Run:** `powershell -ExecutionPolicy Bypass -File [path]`
- **Where:** PC · **File:** `tools/remote-control-server.ps1`
- **Last changed:** 2026-11-19 · **Documented:** no

### `egress_watch.py`

every 5 minutes: is the NUC uploading far more than normal, or is one client pulling

- **Run:** `python3 ~/hwmon/egress_watch.py`
- **Where:** PC · **File:** `tools/security/egress_watch.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `pc_defender_harden.ps1`

Windows Defender hardening for Jason's PC (approved 2026-11-24).

- **Run:** `powershell -NoProfile -ExecutionPolicy Bypass -File [path]`
- **Where:** PC · **File:** `tools/security/pc_defender_harden.ps1`
- **Last changed:** 2026-11-24 · **Documented:** no

### `sudo-crowdsec-bouncer-2026-11-24.sh`

turn on CrowdSec enforcement on the NUC (approved by Jason 11-24).

- **Run:** `sudo bash ~/nuc-homelab/sudo/sudo-crowdsec-bouncer-2026-11-24.sh`
- **Where:** NUC · **File:** `tools/security/sudo-crowdsec-bouncer-2026-11-24.sh`
- **Last changed:** 2026-11-24 · **Documented:** no

### `publish.ps1`

.SYNOPSIS

- **Run:** `.\publish.ps1 -Path "[path]","[path]" -To Novashare`
- **Where:** PC · **File:** `tools/sharedrive/publish.ps1`
- **Last changed:** 2026-11-24 · **Documented:** no

### `run_full.py`

Nightly stag run over the NAS library while the Pictures -> Library move is still going.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/stag/run_full.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `stag_db.py`

Tag the NAS photo library into a local SQLite DB with stag's model (RAM++) plus an NSFW score.

- **Run:** no usage line in the header
- **Where:** NAS · **File:** `tools/stag/stag_db.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `img_diff.py`

T0917: list code files that differ between two images' work dirs (old running image vs candidate).

- **Run:** `img_diff.py <old_image> <new_image> [path=/app]. Prints paths + counts, never file contents.`
- **Where:** PC · **File:** `tools/t0917/img_diff.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `keep_list.py`

T0917 step 3: one keep list for every :before-* image tag (NUC, PC, Pi). Deletes nothing.

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/t0917/keep_list.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `pc_drift.py`

T0917 step 2 (PC): does each running ai-stack/* container run the site-repo code?

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/t0917/pc_drift.py`
- **Last changed:** 2026-11-24 · **Documented:** no

### `t1014_spurts.py`

T1014 phase 2: run nas_move.py on the NAS in 15-minute spurts until the list is done, and

- **Run:** `python [path] ops-2-hold.jsonl journal-2-hold.jsonl`
- **Where:** NAS · **File:** `tools/t1014_spurts.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `test_photo_meta.py`

unit tests for photo_meta.py (T1014). Run: python [path]

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/test_photo_meta.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `todo_api.py`

the /todo page: ONE unified to-do list, kept in a plain file (2026-11-16, v2).

- **Run:** no usage line in the header
- **Where:** PC · **File:** `tools/todo_api.py`
- **Last changed:** 2026-11-20 · **Documented:** no

### `todo_next.py`

todo_next.py (T1017): print the ONE prompt that moves a to-do item to its next part.

- **Run:** `python3 ~/learning-src/todo_next.py T1012`
- **Where:** PC · **File:** `tools/todo_next.py`
- **Last changed:** 2026-11-22 · **Documented:** no

### `todo_renumber.py`

order the whole to-do list by what comes next, then renumber T0001.. in that order.

- **Run:** `python3 ~/learning-src/todo_renumber.py --write`
- **Where:** PC · **File:** `tools/todo_renumber.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `todo_renumber_pc.py`

apply the 2026-11-18 to-do renumber map to the PC-side files that name T-numbers.

- **Run:** `python todo_renumber_pc.py MAP.csv [--write]`
- **Where:** PC · **File:** `tools/todo_renumber_pc.py`
- **Last changed:** 2026-11-19 · **Documented:** no

### `verify_photos.py`

prove which files on [path] already have an identical copy on the NAS. Deletes nothing.

- **Run:** `py -3 -I [path]`
- **Where:** NAS · **File:** `tools/verify_photos.py`
- **Last changed:** 2026-11-19 · **Documented:** no

