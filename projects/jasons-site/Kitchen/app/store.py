"""The drafts table. One SQLite file on the volume, like every other app here.

WHY DRAFTS EXIST AT ALL, GIVEN MEALIE IS RIGHT THERE
----------------------------------------------------
Because importing and keeping are different decisions, and collapsing them
makes a mess. You paste a link, you get a guide, and about a third of the time
the answer is "no, that is not the recipe I meant". If every import went
straight into Mealie, the library would fill up with rejects and the tidying
would be a chore you did later, i.e. never.

So an import lands here, gets a URL you can send to your phone, and stays here
until you press Keep. Then it goes to Mealie and this row is marked kept. What
you never pressed Keep on is swept out after KITCHEN_DRAFT_DAYS.

WHY THE WHOLE RECIPE IS ONE JSON COLUMN
---------------------------------------
Because nothing ever queries inside it. This table answers exactly two
questions -- "give me draft X" and "what have I imported lately" -- and neither
needs ingredients to be rows. Normalising it would buy nothing and cost a
migration every time crawl.py learns a new field.
"""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from . import core

DRAFT_DAYS = 30

# How long a share link works before it stops, on its own, without anyone
# having to remember to revoke it. Jason's own number: "can the link work
# with a 2 day temp access."
SHARE_DAYS = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id          TEXT PRIMARY KEY,
    created     INTEGER NOT NULL,
    title       TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    who         TEXT NOT NULL DEFAULT '',
    recipe      TEXT NOT NULL,
    mealie_slug TEXT NOT NULL DEFAULT ''
);
-- tandoor_id added below via ALTER, same reasoning as the state/who_email/
-- kept_by columns already handled that way -- see the ALTER block further
-- down. A fresh CREATE TABLE here would only apply to a database that does
-- not exist yet; every real one already has the old shape.
CREATE INDEX IF NOT EXISTS drafts_created ON drafts(created DESC);

-- The Mealie password we made up for each person, so we can sign them in
-- without ever asking them for one. See the long note on mealie_login().
--
-- WHY THIS IS ACCEPTABLE TO STORE, when storing passwords normally is not:
-- nobody knows these and nobody ever types one. They are not a credential a
-- person reuses somewhere else; they are an internal handle, closer to an API
-- key than to a password, and they exist only because Mealie has no way to
-- accept "Caddy already checked this person" as an answer. Anyone who can read
-- this file is already inside the container that holds the Mealie admin token,
-- so it grants nothing that was not already reachable.
CREATE TABLE IF NOT EXISTS mealie_users (
    email    TEXT PRIMARY KEY,
    password TEXT NOT NULL,
    made     INTEGER NOT NULL
);

-- One food's nutrition-per-100g, looked up once and kept. Mealie had
-- nowhere of its own to cache this (mealie.py used to stuff it into a
-- JSON string on Mealie's `extras` field); Tandoor has a real Food/
-- Property model but it is not yet clear pushing micronutrients into it
-- buys anything, since nobody but Jason ever opens Tandoor's own pages --
-- Kitchen's own %DV chart on the guide page is what everyone actually
-- reads. So the cache just lives here, keyed by the food's own name, same
-- "look it up once, do arithmetic every time after" reasoning nutrition.py's
-- own docstring already lays out.
CREATE TABLE IF NOT EXISTS food_baselines (
    name    TEXT PRIMARY KEY,
    data    TEXT NOT NULL,
    made    INTEGER NOT NULL
);

-- USDA's own bulk data (Foundation Foods + SR Legacy, imported from their
-- real CSV exports -- see usda_local.py), kept locally so a lookup never
-- depends on the live search API's own relevance ranking, which this
-- session already found unreliable more than once (mint matched to
-- infant formula, ice matched to ice cream sandwich -- see nutrition.py's
-- own comments). Jason's own request (2026-08-24): "grab food items and
-- nutritional values to compare against my models."
CREATE TABLE IF NOT EXISTS usda_local (
    fdc_id      INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    data_type   TEXT NOT NULL,   -- "foundation_food" or "sr_legacy_food"
    nutrients   TEXT NOT NULL    -- JSON, same shape as food_baselines' own data
);

-- usda_local.audit()'s output -- a food whose cached nutrition and this
-- session's local-USDA match disagree by more than 2x, with no
-- automatic call made either way (see usda_local.py's own comment on why
-- auto-fixing this class of discrepancy was tried and abandoned). `cached`
-- and `local` are full baseline dicts (food_baselines' own shape), not
-- just a summary, so approving a row can write the real thing straight
-- back to food_baselines.
CREATE TABLE IF NOT EXISTS nutrition_review (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    cached  TEXT NOT NULL,
    local   TEXT NOT NULL,
    made    INTEGER NOT NULL
);

-- Ingredient lines the three-tier matcher (tandoor.py's
-- resolve_ingredient()) could not confidently place anywhere, even after
-- reading the recipe's own context. Surfaced for Jason to sort out by
-- hand rather than guessed at.
CREATE TABLE IF NOT EXISTS food_warnings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id     TEXT NOT NULL,
    recipe_title TEXT NOT NULL,
    line         TEXT NOT NULL,
    why          TEXT NOT NULL,
    made         INTEGER NOT NULL
);

-- Phi's proposed food/tool duplicates that were NOT confident enough to
-- auto-merge on their own (2026-08-24, Jason's own choice: "conservative
-- auto-merge + review only the unsure ones") -- see dedupe.py. A row here
-- is a PROPOSAL, not a decision; nothing merges on Tandoor's side until
-- Jason approves it in /admin.
CREATE TABLE IF NOT EXISTS merge_proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,   -- "food" or "tool"
    item_id     INTEGER NOT NULL,
    item_name   TEXT NOT NULL,
    target_id   INTEGER NOT NULL,
    target_name TEXT NOT NULL,
    reason      TEXT NOT NULL,
    made        INTEGER NOT NULL
);

-- The little reference card behind a food/tool's picture used to live
-- here -- moved to flat JSON files (see iteminfo.py) per Jason's own
-- explicit, twice-repeated request to manage these as .json files, same
-- convention toolimages.py already uses for a tool's picture reference.

-- Same idea, for Tandoor. Unlike Mealie's row, this one is not filled in on
-- first sight -- Tandoor's sign-up is closed, so Mama's and Casey's accounts
-- were made once by hand and their secrets recorded here to match. See
-- handoff_tandoor.py.
CREATE TABLE IF NOT EXISTS tandoor_users (
    email    TEXT PRIMARY KEY,
    password TEXT NOT NULL,
    made     INTEGER NOT NULL
);

-- The shopping list. ONE list, shared by everybody who can sign in.
--
-- Not one per person, deliberately. A household has one shopping list, on the
-- fridge; two people with private lists is how you come home with two bags of
-- potatoes and no milk. Whoever is at the shop ticks things off and everyone
-- else sees it go.
CREATE TABLE IF NOT EXISTS shopping (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    text     TEXT NOT NULL,
    -- Which recipe it came from, so a line reads "1 litre stock - leek soup"
    -- and you know why it is on the list a week later.
    source   TEXT NOT NULL DEFAULT '',
    draft_id TEXT NOT NULL DEFAULT '',
    done     INTEGER NOT NULL DEFAULT 0,
    added    INTEGER NOT NULL,
    who      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS shopping_done ON shopping(done, added);

-- One row per link in a bulk import, so a long run has somewhere to report
-- progress from and a page can be refreshed without starting it again.
CREATE TABLE IF NOT EXISTS bulk (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    batch    TEXT NOT NULL,
    url      TEXT NOT NULL,
    state    TEXT NOT NULL DEFAULT 'waiting',   -- waiting|working|done|failed
    detail   TEXT NOT NULL DEFAULT '',
    draft_id TEXT NOT NULL DEFAULT '',
    title    TEXT NOT NULL DEFAULT '',
    added    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS bulk_batch ON bulk(batch, id);

-- Did it actually turn out well?
--
-- ONE VOTE PER PERSON PER RECIPE, enforced by the primary key rather than by
-- checking first -- so pressing thumbs-up twice cannot produce two votes, and
-- two people pressing at once cannot race. Changing your mind is an UPSERT,
-- not a second row.
--
-- The vote is the point of the whole app, really: a recipe anybody can paste
-- is worth little, and one three people have cooked and rated is worth a lot.
CREATE TABLE IF NOT EXISTS votes (
    draft_id TEXT NOT NULL,
    email    TEXT NOT NULL,
    vote     INTEGER NOT NULL,          -- +1 or -1, never 0
    at       INTEGER NOT NULL,
    PRIMARY KEY (draft_id, email)
);
CREATE INDEX IF NOT EXISTS votes_draft ON votes(draft_id);

-- What went wrong, what you changed, what to watch for.
--
-- The most valuable thing on a recipe after the method itself: "needs 10 more
-- minutes than it says", "use half the salt", "Casey doesn't like the peppers".
-- None of that is in the source page and none of it survives anywhere else.
CREATE TABLE IF NOT EXISTS comments (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL,
    email    TEXT NOT NULL,
    who      TEXT NOT NULL DEFAULT '',
    text     TEXT NOT NULL,
    at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS comments_draft ON comments(draft_id, at);

-- "Can you write down your fried chicken?" -- a specific ask, sent as a
-- shareable link into the write-your-own-recipe form (POST /mine already
-- carries this id through as a hidden field, see own_save()). The id in
-- the URL being unguessable is what protects this, the same way a draft
-- id is -- not a per-person check, since anyone in the household who can
-- already write a recipe here can already see every request there is.
CREATE TABLE IF NOT EXISTS requests (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    for_email  TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created    INTEGER NOT NULL,
    draft_id   TEXT NOT NULL DEFAULT '',
    fulfilled  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS requests_created ON requests(created DESC);

-- A no-login link to ONE recipe, for a friend who has no account here at
-- all. Jason's own request (2026-08-25): "share the links to a certain
-- recipe to a random person without a login... 2 day temp access."
--
-- A random opaque token, not a signed/stateless one like the auth
-- service's session cookie -- deliberately. This table already gives it
-- for free the two things a stateless token would need extra machinery
-- for: a place to CHECK expiry against without decoding anything (just
-- read the row), and a natural way to revoke one early later if that is
-- ever wanted (delete the row) -- same reasoning merge_proposals and
-- every other "small, queryable fact" table in this file already follows.
CREATE TABLE IF NOT EXISTS shares (
    token      TEXT PRIMARY KEY,
    tandoor_id TEXT NOT NULL,
    title      TEXT NOT NULL,
    made_by    TEXT NOT NULL DEFAULT '',
    created    INTEGER NOT NULL,
    expires    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS shares_expires ON shares(expires);

-- Who has already installed /recipe as an app. Jason's own request
-- (2026-08-26): "if the desktop/mobile app is downloaded check for it
-- and don't run again for that person." A browser-only memory
-- (localStorage) does not follow a PERSON -- install on the phone, then
-- open the same site on a laptop, and a check that only lives in one
-- browser has no way to know the second device is the same household
-- member who already has it elsewhere. Every visitor here is already
-- signed in (recipe-app sits behind forward_auth like everything else),
-- so the real email is always on hand to key this by instead.
CREATE TABLE IF NOT EXISTS installs (
    email    TEXT PRIMARY KEY,
    at       INTEGER NOT NULL
);
"""



@contextmanager
def _db():
    conn = sqlite3.connect(core.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL, so a slow import writing a draft does not block the guide page
    # someone else has open. Every other app on this stack does the same.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with _db() as conn:
        conn.executescript(SCHEMA)
        # ADDED AFTER THE FACT, so it is an ALTER rather than a change to the
        # CREATE above: the drafts table already exists on the volume, and
        # CREATE TABLE IF NOT EXISTS will not add a column to it. Wrapped
        # because "duplicate column" is the normal result on every start after
        # the first, and is not an error worth hearing about.
        for column, default in (("state", "''"), ("who_email", "''"),
                                ("kept_by", "''"), ("tandoor_id", "''")):
            try:
                conn.execute(
                    f"ALTER TABLE drafts ADD COLUMN {column} TEXT NOT NULL DEFAULT {default}")
            except sqlite3.OperationalError:
                pass
        # Jason's own request (2026-08-26): "new ones that get added have
        # a 14 day timer then get moved... out of NEW" -- needs WHEN a
        # recipe actually entered the library, which nothing recorded
        # before now (`created` is when the DRAFT was first imported,
        # often days or weeks before someone actually presses Keep).
        try:
            conn.execute("ALTER TABLE drafts ADD COLUMN kept_at INTEGER NOT NULL DEFAULT 0")
            # Runs ONLY the moment this ALTER itself first succeeds (it
            # fails with "duplicate column" on every start after that,
            # caught below, backfill skipped) -- every recipe already
            # kept before this column existed has no real "entered the
            # library" moment recorded, so `created` is the best
            # available stand-in for those, not a fresh guess.
            conn.execute(
                "UPDATE drafts SET kept_at = created "
                "WHERE kept_at = 0 AND (tandoor_id != '' OR mealie_slug != '')")
        except sqlite3.OperationalError:
            pass


def save(recipe: dict, who: str = "") -> str:
    """Store a freshly imported recipe. Returns its id."""
    draft_id = uuid.uuid4().hex[:12]
    with _db() as conn:
        conn.execute(
            "INSERT INTO drafts (id, created, title, source, who, who_email, recipe) "
            "VALUES (?,?,?,?,?,?,?)",
            (draft_id, int(time.time()), recipe.get("title", "Untitled"),
             recipe.get("source", ""), who.split("@")[0], who.lower(),
             json.dumps(recipe)),
        )
    return draft_id


def get(draft_id: str):
    with _db() as conn:
        row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["recipe"] = json.loads(out["recipe"])
    return out


def get_by_slug(mealie_slug: str):
    """The Kitchen draft behind a Mealie slug, or None if this recipe was
    never imported through Kitchen (added straight in Mealie's own UI, say).
    Callers that need "the guide page for this Mealie recipe" -- the meal
    planner and the random-idea button, which both start from Mealie's own
    library -- fall back to linking Mealie's page directly when this misses.
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM drafts WHERE mealie_slug=? LIMIT 1",
            (mealie_slug,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["recipe"] = json.loads(out["recipe"])
    return out


def update_recipe(draft_id: str, recipe: dict):
    with _db() as conn:
        conn.execute("UPDATE drafts SET recipe=?, title=? WHERE id=?",
                     (json.dumps(recipe), recipe.get("title", "Untitled"), draft_id))


def mark_kept(draft_id: str, slug: str, kept_by: str = ""):
    """Put it in the library, and record who pressed the button.

    Whoever imported a recipe and whoever keeps it are often different people
    -- Mama pastes a link, Casey is the one who actually cooks it and presses
    Keep three weeks later -- so this is its own column rather than reusing
    who_email, which is stamped once at import and never changes.
    """
    with _db() as conn:
        conn.execute(
            "UPDATE drafts SET mealie_slug=?, kept_by=?, kept_at=? WHERE id=?",
            (slug, (kept_by or "").lower(), int(time.time()), draft_id))


def mark_kept_tandoor(draft_id: str, tandoor_id, kept_by: str = ""):
    """Same as mark_kept(), for the Tandoor era. A separate column and
    function rather than reusing mealie_slug -- mealie_slug is a slug
    string, tandoor_id is a numeric id, and keeping them apart means a
    recipe kept under the old system and one kept under the new one are
    never confused for each other while both columns exist side by side.
    """
    with _db() as conn:
        conn.execute(
            "UPDATE drafts SET tandoor_id=?, kept_by=?, kept_at=? WHERE id=?",
            (str(tandoor_id or ""), (kept_by or "").lower(), int(time.time()), draft_id))


def get_by_tandoor_id(tandoor_id):
    """The Kitchen draft behind a Tandoor recipe id, or None. Same
    reasoning as get_by_slug()."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM drafts WHERE tandoor_id=? LIMIT 1",
            (str(tandoor_id),)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["recipe"] = json.loads(out["recipe"])
    return out


def recent(limit: int = 20) -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, created, title, source, mealie_slug, tandoor_id, state, "
            "who, kept_by FROM drafts ORDER BY created DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
#  Votes and comments
# ---------------------------------------------------------------------------
def vote(draft_id: str, email: str, value: int):
    """Record a thumb. Pressing the one you already chose takes it back.

    Toggling rather than only setting, because the button is the same button:
    if thumbs-up is already lit, pressing it means "actually, no" and the
    honest result is no vote at all rather than a vote you cannot undo.
    """
    email = (email or "").lower()
    value = 1 if value > 0 else -1
    with _db() as conn:
        row = conn.execute("SELECT vote FROM votes WHERE draft_id=? AND email=?",
                           (draft_id, email)).fetchone()
        if row and row["vote"] == value:
            conn.execute("DELETE FROM votes WHERE draft_id=? AND email=?",
                         (draft_id, email))
            return 0
        conn.execute(
            "INSERT INTO votes (draft_id, email, vote, at) VALUES (?,?,?,?) "
            "ON CONFLICT(draft_id, email) DO UPDATE SET vote=excluded.vote, "
            "at=excluded.at",
            (draft_id, email, value, int(time.time())))
    return value


def votes_for(draft_id: str, email: str = "") -> dict:
    with _db() as conn:
        up = conn.execute("SELECT COUNT(*) c FROM votes WHERE draft_id=? AND vote>0",
                          (draft_id,)).fetchone()["c"]
        down = conn.execute("SELECT COUNT(*) c FROM votes WHERE draft_id=? AND vote<0",
                            (draft_id,)).fetchone()["c"]
        mine = 0
        if email:
            row = conn.execute("SELECT vote FROM votes WHERE draft_id=? AND email=?",
                               (draft_id, email.lower())).fetchone()
            mine = row["vote"] if row else 0
    return {"up": up, "down": down, "mine": mine}


def vote_counts() -> dict:
    """{draft_id: {up, down}} for every recipe, in one query.

    One query rather than one per card. A browse page showing twenty recipes
    would otherwise be forty round trips to SQLite to render a page that is
    mostly text.
    """
    out = {}
    with _db() as conn:
        for r in conn.execute(
            "SELECT draft_id, "
            "SUM(CASE WHEN vote>0 THEN 1 ELSE 0 END) up, "
            "SUM(CASE WHEN vote<0 THEN 1 ELSE 0 END) down "
            "FROM votes GROUP BY draft_id"
        ):
            out[r["draft_id"]] = {"up": r["up"] or 0, "down": r["down"] or 0}
    return out


def add_comment(draft_id: str, email: str, who: str, text: str) -> bool:
    text = (text or "").strip()[:2000]
    if not text:
        return False
    with _db() as conn:
        conn.execute(
            "INSERT INTO comments (draft_id, email, who, text, at) VALUES (?,?,?,?,?)",
            (draft_id, (email or "").lower(), who, text, int(time.time())))
    return True


def comments_for(draft_id: str) -> list:
    """Oldest first -- a note about a recipe reads as a conversation."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM comments WHERE draft_id=? ORDER BY at ASC",
            (draft_id,)).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        # The date is formatted here rather than in the template, because a
        # template that does date arithmetic is a template nobody can read.
        item["when"] = time.strftime("%-d %b %Y".replace("%-d", "%d"),
                                     time.localtime(item["at"])).lstrip("0")
        out.append(item)
    return out


def drop_comment(comment_id: int, email: str, is_owner: bool = False) -> bool:
    """Your own comment, or anybody's if you are the owner."""
    with _db() as conn:
        if is_owner:
            n = conn.execute("DELETE FROM comments WHERE id=?", (comment_id,)).rowcount
        else:
            n = conn.execute("DELETE FROM comments WHERE id=? AND email=?",
                             (comment_id, (email or "").lower())).rowcount
    return n > 0


def comment_counts() -> dict:
    out = {}
    with _db() as conn:
        for r in conn.execute(
                "SELECT draft_id, COUNT(*) n FROM comments GROUP BY draft_id"):
            out[r["draft_id"]] = r["n"]
    return out


# ---------------------------------------------------------------------------
#  The Mealie sign-in we invented for each person
# ---------------------------------------------------------------------------
def mealie_secret(email: str):
    with _db() as conn:
        row = conn.execute("SELECT password FROM mealie_users WHERE email=?",
                           (email.lower(),)).fetchone()
    return row["password"] if row else None


def set_mealie_secret(email: str, value: str):
    with _db() as conn:
        conn.execute(
            "INSERT INTO mealie_users (email, password, made) VALUES (?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET password=excluded.password, "
            "made=excluded.made",
            (email.lower(), value, int(time.time())))


def forget_mealie_secret(email: str):
    with _db() as conn:
        conn.execute("DELETE FROM mealie_users WHERE email=?", (email.lower(),))


# ---------------------------------------------------------------------------
#  The Tandoor sign-in we invented for each person
# ---------------------------------------------------------------------------
def tandoor_secret(email: str):
    with _db() as conn:
        row = conn.execute("SELECT password FROM tandoor_users WHERE email=?",
                           (email.lower(),)).fetchone()
    return row["password"] if row else None


def set_tandoor_secret(email: str, value: str):
    with _db() as conn:
        conn.execute(
            "INSERT INTO tandoor_users (email, password, made) VALUES (?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET password=excluded.password, "
            "made=excluded.made",
            (email.lower(), value, int(time.time())))


def forget_tandoor_secret(email: str):
    with _db() as conn:
        conn.execute("DELETE FROM tandoor_users WHERE email=?", (email.lower(),))


# ---------------------------------------------------------------------------
#  Nutrition baselines -- looked up once per food, kept
# ---------------------------------------------------------------------------
def food_baseline(name: str):
    with _db() as conn:
        row = conn.execute("SELECT data FROM food_baselines WHERE name=?",
                           (name.strip().lower(),)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["data"])
    except (TypeError, json.JSONDecodeError):
        return None


def food_warning_add(draft_id: str, recipe_title: str, line: str, why: str):
    """An ingredient line nothing could confidently make sense of, for
    Jason's own review -- the "bring it to my notice" tier of the
    three-tier match Jason asked for (2026-08-24): auto-match on a
    confident hit, resolve against the recipe's own context on a doubtful
    one, and land HERE only when even that came back empty.
    """
    with _db() as conn:
        conn.execute(
            "INSERT INTO food_warnings (draft_id, recipe_title, line, why, made) "
            "VALUES (?,?,?,?,?)",
            (draft_id, recipe_title, line, why, int(time.time())))


def food_warnings() -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, draft_id, recipe_title, line, why, made FROM food_warnings "
            "ORDER BY made DESC").fetchall()
    return [dict(r) for r in rows]


def food_warning_clear(warning_id: int):
    with _db() as conn:
        conn.execute("DELETE FROM food_warnings WHERE id=?", (warning_id,))


def merge_proposal_add(kind: str, item_id, item_name: str, target_id,
                       target_name: str, reason: str):
    with _db() as conn:
        conn.execute(
            "INSERT INTO merge_proposals "
            "(kind, item_id, item_name, target_id, target_name, reason, made) "
            "VALUES (?,?,?,?,?,?,?)",
            (kind, item_id, item_name, target_id, target_name, reason, int(time.time())))


def merge_proposals() -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, kind, item_id, item_name, target_id, target_name, "
            "reason, made FROM merge_proposals ORDER BY made DESC").fetchall()
    return [dict(r) for r in rows]


def merge_proposal_get(proposal_id: int):
    with _db() as conn:
        row = conn.execute(
            "SELECT id, kind, item_id, item_name, target_id, target_name, "
            "reason, made FROM merge_proposals WHERE id=?", (proposal_id,)).fetchone()
    return dict(row) if row else None


def merge_proposal_clear(proposal_id: int):
    with _db() as conn:
        conn.execute("DELETE FROM merge_proposals WHERE id=?", (proposal_id,))


def set_food_baseline(name: str, data: dict):
    with _db() as conn:
        conn.execute(
            "INSERT INTO food_baselines (name, data, made) VALUES (?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET data=excluded.data, made=excluded.made",
            (name.strip().lower(), json.dumps(data), int(time.time())))


def usda_local_upsert(fdc_id, description: str, data_type: str, nutrients: dict):
    """One row, added or updated -- unlike usda_local_replace_all(), does
    NOT touch anything else. Jason's own request (2026-08-24): pull a
    single branded product on demand the first time a recipe actually
    needs it ("if something asked for McCormick then we have that
    already in place"), and keep it -- a full-table wipe here would
    throw away every other on-demand pull already made."""
    with _db() as conn:
        conn.execute(
            "INSERT INTO usda_local (fdc_id, description, data_type, nutrients) VALUES (?,?,?,?) "
            "ON CONFLICT(fdc_id) DO UPDATE SET description=excluded.description, "
            "data_type=excluded.data_type, nutrients=excluded.nutrients",
            (fdc_id, description, data_type, json.dumps(nutrients)))


def usda_local_replace_all(rows: list):
    """rows: [{fdc_id, description, data_type, nutrients}]. Wipes and
    reloads only the data_type(s) present in `rows` -- a fresh Foundation/
    SR Legacy bulk import, not a per-row cache, so there is nothing
    incremental to preserve between THOSE. On-demand branded pulls
    (usda_local_upsert, a real per-row cache) live in this same table
    under data_type="branded_food" and must survive a Foundation/SR
    Legacy re-import untouched -- a blanket DELETE here would throw every
    one of them away the next time this runs."""
    data_types = {r["data_type"] for r in rows}
    with _db() as conn:
        if data_types:
            conn.executemany("DELETE FROM usda_local WHERE data_type=?",
                            [(dt,) for dt in data_types])
        conn.executemany(
            "INSERT INTO usda_local (fdc_id, description, data_type, nutrients) VALUES (?,?,?,?)",
            [(r["fdc_id"], r["description"], r["data_type"], json.dumps(r["nutrients"]))
             for r in rows])


def usda_local_all() -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT fdc_id, description, data_type, nutrients FROM usda_local").fetchall()
    out = []
    for r in rows:
        try:
            nutrients = json.loads(r["nutrients"])
        except (TypeError, json.JSONDecodeError):
            nutrients = {}
        out.append({"fdc_id": r["fdc_id"], "description": r["description"],
                    "data_type": r["data_type"], "nutrients": nutrients})
    return out


def usda_local_count() -> int:
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) c FROM usda_local").fetchone()["c"]


def nutrition_review_replace_all(rows: list):
    """Wipes and reloads -- this is a fresh usda_local.audit() run, not a
    per-row cache, same reasoning as usda_local_replace_all()."""
    with _db() as conn:
        conn.execute("DELETE FROM nutrition_review")
        conn.executemany(
            "INSERT INTO nutrition_review (name, cached, local, made) VALUES (?,?,?,?)",
            [(r["name"], json.dumps(r["cached"]), json.dumps(r["local"]), int(time.time()))
             for r in rows])


def nutrition_review_all() -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, name, cached, local FROM nutrition_review ORDER BY made DESC").fetchall()
    out = []
    for r in rows:
        try:
            cached = json.loads(r["cached"])
            local = json.loads(r["local"])
        except (TypeError, json.JSONDecodeError):
            continue
        out.append({"id": r["id"], "name": r["name"], "cached": cached, "local": local})
    return out


def nutrition_review_get(review_id: int):
    with _db() as conn:
        row = conn.execute(
            "SELECT id, name, cached, local FROM nutrition_review WHERE id=?",
            (review_id,)).fetchone()
    if not row:
        return None
    try:
        cached = json.loads(row["cached"])
        local = json.loads(row["local"])
    except (TypeError, json.JSONDecodeError):
        return None
    return {"id": row["id"], "name": row["name"], "cached": cached, "local": local}


def nutrition_review_clear(review_id: int):
    with _db() as conn:
        conn.execute("DELETE FROM nutrition_review WHERE id=?", (review_id,))


# ---------------------------------------------------------------------------
#  The shopping list
# ---------------------------------------------------------------------------
def shopping_add(lines: list, source: str = "", draft_id: str = "", who: str = ""):
    """Add ingredient lines. Returns how many were actually new.

    ALREADY-PRESENT LINES ARE SKIPPED, matching on the text. Adding the same
    recipe twice is a thing people do -- you add it, forget, and add it again
    from the other phone -- and it should not produce the list twice. Matching
    is exact rather than clever: "2 onions" and "1 onion" are different lines
    and a shopper can see that for themselves. Guessing that they are the same
    thing and merging them into "3 onions" is the kind of helpfulness that
    ends up wrong in a way nobody can unpick at the shop.
    """
    added = 0
    with _db() as conn:
        have = {r["text"] for r in conn.execute(
            "SELECT text FROM shopping WHERE done=0").fetchall()}
        for line in lines:
            line = (line or "").strip()
            if not line or line in have:
                continue
            conn.execute(
                "INSERT INTO shopping (text, source, draft_id, added, who) "
                "VALUES (?,?,?,?,?)",
                (line, source, draft_id, int(time.time()), who.lower()))
            have.add(line)
            added += 1
    return added


def shopping_all() -> list:
    """Everything on the list. Unticked first, then ticked, each oldest-first.

    Ticked items STAY VISIBLE rather than vanishing. Watching the list shrink
    is how you know you are nearly done, and an item that disappears the
    instant you tap it is impossible to un-tap when you tapped the wrong one.
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM shopping ORDER BY done ASC, added ASC").fetchall()
    return [dict(r) for r in rows]


def shopping_toggle(item_id: int):
    with _db() as conn:
        conn.execute("UPDATE shopping SET done = 1 - done WHERE id=?", (item_id,))


def shopping_clear_done() -> int:
    with _db() as conn:
        cur = conn.execute("DELETE FROM shopping WHERE done=1")
        return cur.rowcount


def shopping_remove(item_id: int):
    with _db() as conn:
        conn.execute("DELETE FROM shopping WHERE id=?", (item_id,))


def shopping_pending() -> int:
    with _db() as conn:
        row = conn.execute("SELECT COUNT(*) c FROM shopping WHERE done=0").fetchone()
    return row["c"] if row else 0


# ---------------------------------------------------------------------------
#  Bulk imports
# ---------------------------------------------------------------------------
def bulk_queue(batch: str, urls: list):
    with _db() as conn:
        for url in urls:
            conn.execute(
                "INSERT INTO bulk (batch, url, added) VALUES (?,?,?)",
                (batch, url, int(time.time())))


def bulk_next(batch: str):
    """Claim the next waiting link, marking it working in the same breath.

    One statement, so two workers cannot claim the same row. There is only one
    worker today, but "there is only one" is not a property that survives the
    next change, and the fix costs nothing now and a confusing double-import
    later.
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT id, url FROM bulk WHERE batch=? AND state='waiting' "
            "ORDER BY id LIMIT 1", (batch,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE bulk SET state='working' WHERE id=? AND state='waiting'",
                     (row["id"],))
    return dict(row)


def bulk_finish(row_id: int, state: str, detail: str = "",
                draft_id: str = "", title: str = ""):
    with _db() as conn:
        conn.execute(
            "UPDATE bulk SET state=?, detail=?, draft_id=?, title=? WHERE id=?",
            (state, detail[:300], draft_id, title, row_id))


def bulk_rows(batch: str) -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM bulk WHERE batch=? ORDER BY id", (batch,)).fetchall()
    return [dict(r) for r in rows]


def kept_ids(ids: list) -> set:
    """Which of these draft ids are already in the library.

    One query for a whole batch's worth, so a bulk-import results page can ask
    "how far along is Keep-all" without a round trip per row.
    """
    ids = [i for i in (ids or []) if i]
    if not ids:
        return set()
    with _db() as conn:
        q = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id FROM drafts WHERE id IN ({q}) AND mealie_slug != ''",
            ids).fetchall()
    return {r["id"] for r in rows}


# ---------------------------------------------------------------------------
#  Labels
# ---------------------------------------------------------------------------
def all_labels() -> list:
    """Every label in use, most-used first, with counts.

    Read out of the drafts' JSON rather than kept in its own table. The list is
    a few hundred rows and this runs when a page renders, so the join a table
    would save is not worth the second place for the truth to live.
    """
    counts = {}
    with _db() as conn:
        for row in conn.execute("SELECT recipe FROM drafts").fetchall():
            try:
                for label in (json.loads(row["recipe"]).get("labels") or []):
                    label = str(label).strip().lower()
                    if label:
                        counts[label] = counts.get(label, 0) + 1
            except (json.JSONDecodeError, AttributeError):
                continue
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def cook_counts() -> list:
    """How many recipes each person put here, most first.

    Read from the who_email COLUMN rather than from the recipe JSON, because
    that is where store.save() records it and a second copy would be a second
    thing to keep in step.
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT who_email, COUNT(*) n FROM drafts "
            "WHERE who_email != '' GROUP BY who_email ORDER BY n DESC"
        ).fetchall()
    return [(r["who_email"], r["n"]) for r in rows]


def by_cook(email: str, limit: int = 60) -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, created, title, source, mealie_slug, state, who, "
            "who_email, recipe FROM drafts WHERE who_email=? "
            "ORDER BY created DESC LIMIT ?", ((email or "").lower(), limit)
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            recipe = json.loads(item.pop("recipe"))
        except (json.JSONDecodeError, TypeError):
            recipe = {}
        item["labels"] = [str(x).strip().lower()
                          for x in (recipe.get("labels") or [])]
        item["course"] = recipe.get("course", "")
        item["cuisine"] = recipe.get("cuisine", "")
        out.append(item)
    return out


def all_kept() -> list:
    """{mealie_slug, recipe} for every draft actually in the library.

    For admin tooling that needs to push something FROM Kitchen's own copy
    TO Mealie across the whole library at once -- see mealie_fix.py's
    --tags command, which uses this to backfill tags/category/tools onto
    recipes that were imported before this app started sending them.
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT mealie_slug, recipe FROM drafts "
            "WHERE mealie_slug != '' ORDER BY created").fetchall()
    out = []
    for row in rows:
        try:
            recipe = json.loads(row["recipe"])
        except (json.JSONDecodeError, TypeError):
            continue
        out.append({"mealie_slug": row["mealie_slug"], "recipe": recipe})
    return out


def kept_with_ids() -> list:
    """{id, mealie_slug, tandoor_id, kept_at, recipe} for every draft
    actually in the library -- either library, during the migration.

    Same query as all_kept(), plus the draft id -- for admin tooling that
    needs to call back into get()/update_recipe() per recipe (drawing missing
    step pictures, say), not just read them.
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, mealie_slug, tandoor_id, kept_at, recipe FROM drafts "
            "WHERE mealie_slug != '' OR tandoor_id != '' ORDER BY created").fetchall()
    out = []
    for row in rows:
        try:
            recipe = json.loads(row["recipe"])
        except (json.JSONDecodeError, TypeError):
            continue
        out.append({"id": row["id"], "mealie_slug": row["mealie_slug"],
                    "tandoor_id": row["tandoor_id"], "kept_at": row["kept_at"],
                    "recipe": recipe})
    return out


def cards(limit: int = 24, ids: list = None) -> list:
    """Everything a card needs, in ONE pass over the drafts.

    A card wants the photo, the pills, the course and who added it -- all of
    which live inside the recipe JSON. Fetching them per card would be a query
    per tile; this reads the rows once and unpacks in Python, which for a few
    hundred recipes is faster than the round trips and far easier to read.
    """
    with _db() as conn:
        if ids is not None:
            if not ids:
                return []
            q = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT id, created, title, source, mealie_slug, tandoor_id, "
                f"state, who_email, kept_by, recipe FROM drafts WHERE id IN ({q}) "
                f"ORDER BY created DESC", ids).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, created, title, source, mealie_slug, tandoor_id, "
                "state, who_email, kept_by, recipe FROM drafts ORDER BY created DESC "
                "LIMIT ?", (limit,)).fetchall()

    out = []
    for row in rows:
        item = dict(row)
        try:
            recipe = json.loads(item.pop("recipe"))
        except (json.JSONDecodeError, TypeError):
            recipe = {}
        mine = recipe.get("mine") or {}
        # YOUR photo wins over the scraped one, same order as the guide page.
        # A card showing the stock photo of a dish somebody has photographed
        # themselves is the app forgetting what it was told.
        item["own_photo"] = mine.get("hero", "")
        item["image"] = recipe.get("image", "")
        item["labels"] = [str(x).strip().lower()
                          for x in (recipe.get("labels") or [])]
        item["course"] = recipe.get("course", "")
        item["cuisine"] = recipe.get("cuisine", "")
        item["yields"] = recipe.get("yields", "")
        item["steps"] = len(recipe.get("steps") or [])
        out.append(item)
    return out


def all_ingredients(limit: int = 200) -> list:
    """Every recipe as {id, title, ingredients}, for the basket matcher."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, title, recipe FROM drafts ORDER BY created DESC LIMIT ?",
            (limit,)).fetchall()
    out = []
    for r in rows:
        try:
            recipe = json.loads(r["recipe"])
        except (json.JSONDecodeError, TypeError):
            continue
        out.append({"id": r["id"], "title": r["title"],
                    "ingredients": recipe.get("ingredients") or []})
    return out


def field_counts(field: str) -> list:
    """How many recipes carry each value of `course` or `cuisine`.

    Same shape as all_labels() so the browse page can render a row of pills
    from either without caring which it is.
    """
    counts = {}
    with _db() as conn:
        for row in conn.execute("SELECT recipe FROM drafts").fetchall():
            try:
                value = str(json.loads(row["recipe"]).get(field) or "").strip().lower()
            except (json.JSONDecodeError, AttributeError):
                continue
            if value:
                counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def with_field(field: str, value: str, limit: int = 60) -> list:
    value = (value or "").strip().lower()
    out = []
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, created, title, source, mealie_slug, state, who, recipe "
            "FROM drafts ORDER BY created DESC").fetchall()
    for row in rows:
        try:
            recipe = json.loads(row["recipe"])
        except (json.JSONDecodeError, AttributeError):
            continue
        if str(recipe.get(field) or "").strip().lower() != value:
            continue
        item = dict(row)
        item.pop("recipe", None)
        item["labels"] = [str(x).strip().lower()
                          for x in (recipe.get("labels") or [])]
        item["course"] = recipe.get("course", "")
        item["cuisine"] = recipe.get("cuisine", "")
        out.append(item)
        if len(out) >= limit:
            break
    return out


def with_label(label: str, limit: int = 60) -> list:
    label = (label or "").strip().lower()
    out = []
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, created, title, source, mealie_slug, state, who, recipe "
            "FROM drafts ORDER BY created DESC").fetchall()
    for row in rows:
        try:
            labels = [str(x).strip().lower()
                      for x in (json.loads(row["recipe"]).get("labels") or [])]
        except (json.JSONDecodeError, AttributeError):
            continue
        if label in labels:
            item = dict(row)
            recipe = json.loads(row["recipe"])
            item.pop("recipe", None)
            item["labels"] = labels
            item["course"] = recipe.get("course", "")
            item["cuisine"] = recipe.get("cuisine", "")
            out.append(item)
            if len(out) >= limit:
                break
    return out


def sweep():
    """Delete old drafts that were never kept.

    Runs at startup rather than on a timer. This app is restarted whenever the
    stack is, which on this machine is often enough, and a background task is
    one more thing that can be quietly dead for a month without anyone noticing.
    """
    # CAUGHT LIVE, before it ever ran on a real Tandoor-kept recipe: this
    # used to check mealie_slug='' alone. A recipe kept through Tandoor has
    # an empty mealie_slug (it was never touched) but a real tandoor_id --
    # so the old check would have read every Tandoor-kept recipe as "never
    # kept" and deleted it 30 days later. Both columns have to be empty.
    cutoff = int(time.time()) - DRAFT_DAYS * 86400
    with _db() as conn:
        cur = conn.execute(
            "DELETE FROM drafts WHERE created < ? AND mealie_slug='' AND tandoor_id=''",
            (cutoff,))
        # Expired share links, tidied up here too rather than left to grow
        # forever -- share_get() already refuses an expired row on its own,
        # so this is housekeeping, not a security check.
        conn.execute("DELETE FROM shares WHERE expires < ?", (int(time.time()),))
        return cur.rowcount


# ---------------------------------------------------------------------------
#  Share links -- a recipe, no account required
# ---------------------------------------------------------------------------
def share_create(tandoor_id, title: str, who: str = "") -> str:
    """A fresh, unguessable token good for SHARE_DAYS. Returns the token
    itself -- main.py builds the full URL, since core.SITE_ROOT is not
    this module's business."""
    token = uuid.uuid4().hex
    now_ts = int(time.time())
    with _db() as conn:
        conn.execute(
            "INSERT INTO shares (token, tandoor_id, title, made_by, created, expires) "
            "VALUES (?,?,?,?,?,?)",
            (token, str(tandoor_id), title, who.lower(), now_ts, now_ts + SHARE_DAYS * 86400))
    return token


def share_get(token: str):
    """The recipe a share link points to, or None if the token is wrong,
    unknown, or has expired. Expiry is checked HERE, not left to the
    caller, so there is exactly one place that can get it wrong."""
    with _db() as conn:
        row = conn.execute(
            "SELECT token, tandoor_id, title, made_by, created, expires "
            "FROM shares WHERE token=?", (token,)).fetchone()
    if not row:
        return None
    row = dict(row)
    if row["expires"] <= int(time.time()):
        return None
    return row


# ---------------------------------------------------------------------------
#  Who has installed /recipe as an app -- see the `installs` table's own
#  comment in SCHEMA for why this is per-person, not per-browser.
# ---------------------------------------------------------------------------
def mark_installed(email: str):
    email = (email or "").strip().lower()
    if not email:
        return
    with _db() as conn:
        conn.execute(
            "INSERT INTO installs (email, at) VALUES (?,?) "
            "ON CONFLICT(email) DO NOTHING",
            (email, int(time.time())))


def is_installed(email: str) -> bool:
    email = (email or "").strip().lower()
    if not email:
        return False
    with _db() as conn:
        row = conn.execute("SELECT 1 FROM installs WHERE email=?", (email,)).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
#  Recipe requests -- "can you write down your fried chicken?"
# ---------------------------------------------------------------------------
def request_add(title: str, note: str, for_email: str, created_by: str) -> str:
    """A new ask, with a shareable link. Returns its id."""
    req_id = uuid.uuid4().hex[:12]
    with _db() as conn:
        conn.execute(
            "INSERT INTO requests (id, title, note, for_email, created_by, created) "
            "VALUES (?,?,?,?,?,?)",
            (req_id, title.strip(), note.strip(), for_email.strip().lower(),
             created_by.strip().lower(), int(time.time())))
    return req_id


def request_get(req_id: str):
    with _db() as conn:
        row = conn.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
    return dict(row) if row else None


def requests_all() -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM requests ORDER BY created DESC").fetchall()
    return [dict(r) for r in rows]


def request_fulfill(req_id: str, draft_id: str):
    """The recipe came in. Best-effort by design -- called right after
    own_save() has already written the recipe itself, so a bad request id
    here (the row was deleted, say) must never look like the SAVE failed."""
    with _db() as conn:
        conn.execute(
            "UPDATE requests SET draft_id=?, fulfilled=? WHERE id=?",
            (draft_id, int(time.time()), req_id))
