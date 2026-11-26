# brand-sources/

The logo files these cards' icons were built from, kept so the next person can
see where each mark came from and regenerate it.

**Every one was pulled out of the container that actually runs the app** —
Tandoor's from Tandoor, Grafana's from Grafana — rather than drawn from memory.
A hand-drawn brand mark is not that brand's logo; it is a wrong one.

| File | App | How it was obtained |
|---|---|---|
| `tandoor.svg` | Tandoor Recipes | `GET /favicon.svg` |
| `gitea.svg` | Gitea | `GET /assets/img/favicon.svg` |
| `grafana.svg` | Grafana | `docker cp` `/usr/share/grafana/public/img/grafana_icon.svg` |
| `paperless.svg` | Paperless-ngx | `docker cp` the frontend's `logo-notext.svg` |
| `immich.png` | Immich | `docker cp /build/www/favicon.png` |
| `ittools.png` | IT-Tools | `GET /favicon-32x32.png` |
| `opengym.png` | openGym | `GET /icon-180.png` |
| `homepage.png` | Homepage | `docker cp /app/public/android-chrome-192x192.png` |

The four PNGs are raster because **those projects ship no vector mark anywhere
in their images** — checked, not assumed. They are downscaled to 64px and
embedded as data URIs. A real logo slightly soft beats a wrong logo drawn
sharply.

**Portainer, Dozzle and Stirling are missing on purpose.** Portainer and Dozzle
are distroless (no shell, and `docker cp` found nothing at the usual paths) and
Stirling's image carries only Ubuntu's and LibreOffice's logos. Those three
cards keep their hand-drawn glyph rather than wearing a guess.

Regenerate with `scratchpad/mkbrand.py`, which writes `src/brand.rs`.
