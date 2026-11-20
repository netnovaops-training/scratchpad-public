# Admin — site data

This folder is where the admin at `https://example.org/admin/` puts
the JSON you upload. These are ordinary files: open them, copy them, back them
up, or edit one by hand when that's faster than a browser.

```
Admin\
  data\        <- live. The sites read these.
  backups\     <- the previous 10 versions of each file, kept automatically
```

## Why it lives here and not in Docker

It used to be a Docker named volume. The files were real but invisible — not in
any folder you could open, not in a backup you'd think to take, and only
reachable with a `docker cp`. Now they're where you'd expect them.

## What's in data\

| File | Site | What it holds |
|---|---|---|
| `booking.json` | `/dee` | Ms Dee — location, hours, service area, rates |
| `mom.json` | `/mama` | Mama — tech help content |
| `mom-kb.json` | `/mama` | **Known fixes.** Answers already written, used instead of asking a model. See below. |
| `legacy.json` | `/legacy` | **Which photo folders are published.** See below. |
| `dashboard.json` | `/dashboard` | Panels, sources and layout |
| `jason.json` | `/jason` | Projects and write-ups |
| `places.json` | shared | Somewhere-to-be listings, added to from a browser |

A file that isn't there yet just means that site is running on its built-in
defaults. That's a normal state, not an error.

`/casey` isn't in that list any more — it manages its own content, so an upload
page for it was a door onto a room with nothing in it.

## Two ways to change a file

The admin has two kinds of page, and picking the wrong one is the only way to
lose data here.

**Replace a whole file** — the original upload. What you send *becomes* the
file. Right for `legacy.json`, `dashboard.json`, anything you rewrite in one go.

**Slipstream** — add and remove one entry at a time. You send a *patch*:

```json
{ "add":    [ { "name": "Somewhere New", "category": "libraries" } ],
  "remove": [ "a-place-that-closed" ] }
```

Everything you don't name is left exactly as it was, including hand-written
keys at the top of the file. There is a paste box as well as a file picker, so
"ask a model for this cafe as JSON, paste it in" is the whole job.

Two files have a slipstream page: `places.json` and `mom-kb.json`. Both are
lists that grow, which is what makes replacing the whole file the wrong shape
for them — one stale copy and forty entries are gone.

### How entries are matched

Every entry has an `id`. If your patch doesn't carry one, it's worked out from
the entry's own `name` (places) or `title` (knowledge base) — so
`"Walnut Recreation Center"` becomes `walnut-recreation-center`.

That single rule is what makes this safe to use casually: **sending the same
entry twice updates it, it never duplicates it.** Found a better phone number?
Paste the whole entry again with the number fixed. Want to rename something
without it being treated as a new entry? Give it an explicit `id` and change
the name freely.

Removal is by `id` too, never by position, so it can't quietly delete the wrong
row. The page lists what's in the file with a Remove button on each one.

Two more things the page does that are easy to miss:

* **Top-level keys you wrote by hand survive a patch.** A `_note` at the top of
  the file is still there afterwards.
* **The `_readme` block in the example files is not copied in.** Paste an
  example as-is; only its `add` and `remove` are acted on.

If you really do want to rewrite one of these two files in one go, there's a
**Replace the whole file** section folded up at the bottom of the page. It's
folded up because it's the one action here that can lose entries.

Start from `places.example.json` and `mom-kb.example.json` in this folder —
they're commented and they're valid patches, so they can be pasted in as-is.

## mom-kb.json answers without a model

This one is worth understanding because it changes what `/mama` *does*.

Each article is a problem you have already solved, with `symptoms` — the
phrases she would actually use — and the steps that fixed it. When what she
asks matches an article strongly enough, the answer comes straight from this
file:

* instantly, with **no model call and no waking the desktop**
* word for word as you wrote it, the same every time

Matching is plain word overlap against your `symptoms`, not an embedding, and
that's deliberate: embeddings need the GPU, which is the thing this is trying
to avoid. If an article isn't matching, add the sentence she actually typed to
its `symptoms`.

**`"direct": true` is what allows an article to answer on its own.** Leave it
out and the article is still used — handed to the model as a known-good fix to
follow — but it never becomes the reply by itself. It's opt-in so an article
written in a hurry gets the cautious behaviour until you've read it back.

`/mama`'s own admin has a **Known fixes** page: it shows what loaded, and has a
box where you type a question and see exactly which article would answer it and
by how much. That's the tool for tuning a symptom line.

## places.json — written, not yet read

Be clear about where this one stands: **the admin writes `places.json`, and
nothing reads it yet.** The phone line answers from `ai-stack\config\places.yaml`
and `places.local.yaml`, which is what `places_import.py` maintains. Adding a
place here is safe and it's kept, but it will not show up on a call until the
phone line is taught to read this file as a layer on top of the YAML.

That's a small change and worth making — the YAML is the hand-checked base and
this would be the from-a-browser layer over it. It just hasn't been made, and a
README that said otherwise would have you adding places for weeks and wondering
why nobody was ever sent to one.

`category` is worth getting right now anyway, because it decides which section
an entry joins and the phone line treats `parks` as outdoors — hidden in the
Las Vegas summer. No category lands in `indoor_summer`, which is the safe
default rather than the flattering one.

## legacy.json decides what's visible

This one is doing security work, so it's worth understanding. It names the
photo *folders* that are published:

```json
{
  "title": "PeeWee Legacy",
  "intro": "Family photos, kept somewhere they won't be lost.",
  "albums": [
    { "id": "portraits", "title": "Final portraits",
      "folder": "Photos-1-001 (1)/Final_Portraits" }
  ]
}
```

**A folder not listed here has no URL at all.** Not hidden, not blocked —
there's no address that reaches it. Add a folder to publish it; remove it to
un-publish it. `id` is what appears in the URL, so keep it short and don't
change it once you've shared a link.

## Editing by hand

Fine — the sites re-read these files on each request, so a save is live
immediately with no restart. Two rules:

1. **It must be valid JSON.** The admin refuses a bad upload; editing directly
   here bypasses that check. If a site suddenly shows defaults, that's the
   likely cause — paste the file into any JSON validator.
2. **Editing here doesn't create a backup.** Only uploads do. Copy the file
   first if you're doing something you might want to undo.

## Backups

Slipstream patches make a backup too — one per apply, and one per Remove
button. So the undo for "I removed the wrong place" is the same as the undo for
a bad upload: take the newest file out of `backups\`.

`backups\` holds the last 10 versions of each file, named
`20260821-143355-legacy.json`. To roll back, copy one over the live file in
`data\` and drop the timestamp from the name.

## Worth backing up

This folder and `caddy_data` (your certificates) are the two things in this
stack that can't be regenerated. Everything else rebuilds from source.
