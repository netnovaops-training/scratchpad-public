# Python versions (2026-11-16)

Target for PC scripts: **3.14.10**. Written by `tools/py_versions.py`; re-run monthly.

## Interpreters on this PC

| Version | Free-threaded | Packages | Only in this one (vs target) | Path |
|---|---|---|---|---|
| 3.14.10 | no | 97 | 0 | `C:\Python314\python.exe` |
| 3.12.10 | no | 266 | 197: accelerate, aiocache, aiodns, aiofiles, aiohappyeyeballs, aiohttp, aiosignal, aiosqlite, alembic, anthropic, antlr4-python3-runtime, apscheduler ... | `C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe` |
| 3.11.9 | no | 2 | 0 | `C:\Users\USER\AppData\Local\Programs\Python\Python311\python.exe` |

No free-threaded build is installed (it is a separate `python3.14t.exe`, an installer option).

## Docker base images

| File | Python |
|---|---|
| `colibri\docker\Dockerfile.fetch` | 3.15 |
| `collage\Dockerfile` | 3.15 |
| `dj\Dockerfile` | 3.15 |
| `garden-ingest\Dockerfile` | 3.12 |
| `hermes\Dockerfile` | 3.15 |
| `index-feed.ps1 (docker run)` | 3.12 |
| `metrics\Dockerfile` | 3.15 |
| `peewee-tools\studio\Dockerfile` | 3.15 |
| `pi-digits\Dockerfile` | 3.12 |
| `printq\Dockerfile` | 3.15 |
| `stt\Dockerfile.cpu` | 3.14 |
| `tables\Dockerfile` | 3.15 |
| `telegram\Dockerfile` | 3.15 |
| `telephony\Dockerfile` | 3.15 |
| `voice\Dockerfile.cpu` | 3.14 |
| `watchdog\Dockerfile` | 3.15 |
| `web\Dockerfile.app` | 3.15 |
| `websites.ps1 (docker run)` | 3.12 |

## Scripts that would not run on 3.14.10 (15 of 179)

| Script | Problem |
|---|---|
| `pc/doc-gather/debug_one.py` | missing: gather |
| `pc/doc-gather/fold_in.py` | missing: gather |
| `pc/doc-gather/sort_new.py` | missing: gather |
| `pc/ai-stack/scripts/docs_status.py` | missing: doc_backfill |
| `pc/ai-stack/scripts/doc_sidefx.py` | missing: doc_backfill |
| `pc/ai-stack/scripts/import_graph.py` | missing: doc_backfill |
| `pc/ai-stack/scripts/seed-plant-pages.py` | missing: garden_ingest, pymysql |
| `pc/ai-stack/shared/router.py` | missing: openai |
| `pc/ai-stack/scratchpad/files/append_tsv.py` | missing: extract_strings |
| `pc/ai-stack/scratchpad/files/mine/add_spec.py` | missing: cins |
| `pc/ai-stack/scratchpad/files/mine/ai_tabs_spec.py` | missing: cins |
| `pc/ai-stack/scratchpad/files/mine/form_spec.py` | missing: cins |
| `pc/ai-stack/scratchpad/files/mine/pipeline_spec.py` | missing: cins |
| `pc/ai-stack/scratchpad/files/mine/ref.py` | missing: cins |
| `pc/ai-stack/scratchpad/files/mine/wl_spec.py` | missing: cins |

## Pinned to an older version (python-pins.json)

None.

## Scripts that run inside Docker (judged by their image, not this PC)

- Python 3.12: 3 scripts
- Python 3.15: 46 scripts
- Python image: 6 scripts

## Free-threaded candidates

Scripts that use threads. They only gain from the no-GIL build if the threads do CPU work (network or disk waits already overlap with the GIL). Test one on `python3.14t` before moving it.

- `pc/ai-stack/shared/transcript.py`

## NUC

```
/usr/bin/python3 Python 3.14.4
/usr/bin/python3.14 Python 3.14.4
```
