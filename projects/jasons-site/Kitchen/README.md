# Kitchen

Paste a link, get a recipe you can actually follow.

Served at `example.org/kitchen`, behind the same sign-in as `/mama`
and `/casey`. The recipe **library** is Mealie, next door on port 9443; this is
the front door that reads pages and hands them over.

**The full write-up is `ai-stack\RECIPES.md`** — how it works, how to run it,
what to do when something breaks. This file is just the map.

```
app\core.py       config, and the SSRF guard on fetched URLs
app\crawl.py      fetching, schema.org extraction, image harvesting
app\importer.py   the order of operations; matching photos to steps
app\scale.py      portion arithmetic — no model touches this, ever
app\subs.py       the substitutions table, then the model
app\diagram.py    the drawn step diagrams
app\mealie.py     the thin Mealie client — three endpoints, no more
app\store.py      drafts, in SQLite on the kitchen_data volume
app\main.py       routes
test_kitchen.py   the tests
```

Every file's docstring says *why* it is built the way it is. If you only read
one, read `scale.py` — it is the clearest statement of the rule the whole app
follows: **do it in code if code can be exact, and only then ask the model.**

```powershell
# rebuild after an edit
docker compose -f docker-compose.yml -f docker-compose.web.yml build kitchen
docker compose -f docker-compose.yml -f docker-compose.web.yml --profile web up -d kitchen

# tests: no GPU, no network, about a second
docker run --rm --entrypoint python ai-stack/kitchen:latest /app/test_kitchen.py
```

## Thumbs, notes and cards

Every recipe listing is a card: the photo, the course and cuisine, up to three
pills, and — once anybody has voted — a running tally in the corner of the
picture. One partial (`templates/_card.html`) draws all of them, so the home
page and browse cannot drift apart.

On a recipe itself, **Did it turn out?** takes one thumb per person. Pressing
the thumb that is already lit takes the vote back; the primary key on the
`votes` table is `(recipe, person)`, so one-vote-per-person is enforced by the
database rather than by remembering to check. Notes underneath are for what you
changed and what to watch for. You can remove your own; Jason can remove any.

## Step pictures

A step with no photograph gets a diagram. Steps worth more than a diagram can
be drawn: **Draw pictures for the N steps without one** on any recipe (Jason
only), and recipes already in the library get filled in on their own in the
background.

Three things are worth knowing about them:

* **They are drawings, not photographs**, and every one is labelled that way
  on the page. They get the scene right — a pan of onions on a hob — more
  reliably than the state, so a step about raw chicken may show it browner
  than it should be. Your own photo replaces one at any time and always wins.
* **Nothing is downloaded from the web.** Searching for a picture that fits a
  step gives you somebody else's dinner in somebody else's pan, usually from a
  different recipe, and hotlinks their copyrighted photograph onto a page that
  implies it is yours.
* **They never take the graphics card from anything interactive.** The drawing
  service refuses to load unless there is comfortable room free, and hands the
  card back the moment a recipe is done rather than after a timer. If the
  phone line is busy, pressing the button says so and draws nothing.

Turn the background pass off with `KITCHEN_DRAW_IDLE=0`. The service itself is
`ai-stack/imagine` — see `ai-stack/imagine/README.md`.

## "Might go well with this"

The first time anyone opens a recipe's guide, it asks the model for up to
four small additions — an extra protein, a spice, a side — that fit that
particular dish, and caches the answer so it costs nothing on every later
view. It never touches an import in bulk; only recipes somebody actually
opens ever ask the model for this.

Every suggestion sits under one line, always shown, never something the model
can leave out: **AI suggested — not tested in this kitchen, use your own
judgement, at your own risk.** That line is added in code after the model has
answered, the same way `subs.py` appends its allergy warning — so it cannot
be missing just because the model forgot to say it that time.
