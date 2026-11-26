# rust
The Rust workspace: the site's own web apps, all built from one Dockerfile.

| Item | What it is | How it runs |
|---|---|---|
| landing/ | the main site landing page (src, templates, static, brand-sources) | compose service `landing` (`APP: landing`) |
| admin/ | admin console (src, templates, tests) | compose service `admin` (`APP: admin`) |
| auth/ | household sign-in / SSO gate | compose service `auth` (`APP: auth`) |
| legacy/ | the Legacy photo browser (replaced Caddy file_server) | compose service `legacy` (`APP: legacy`) |
| scratchpad/ | code scratchpad at /scratchpad/ | runs on the NUC (fed by scripts/sync_scratchpad.py) |
| common/ | shared library: people, router, trial | compiled into the apps |
| Cargo.toml / Cargo.lock | workspace definition and lockfile | `cargo build` |
| Dockerfile / .dockerignore | one Dockerfile for every app, picked by build arg `APP` | built by docker-compose.web.yml (`context: ./rust`) |
| target/ | cargo build output (~26k files) | generated; safe to delete, rebuilds |

Related: docker-compose.web.yml services `landing`, `admin`, `auth`, `legacy`; mounts from ./site-data; `rust.cmd`, `rust-setup.ps1` in ai-stack root.
Docs: ai-stack\docs\code\rust\
Updated: 2026-11-13
