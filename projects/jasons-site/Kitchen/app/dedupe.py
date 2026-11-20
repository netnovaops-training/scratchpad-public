"""Finding and merging duplicate foods/tools, Jason's own request
(2026-08-24): "group into simple and similar food items and tools",
with his own explicit choice on approval afterward -- "conservative
auto-merge, review only the unsure ones" (near-identical/plural/filler
variants merge with no review; anything that needed real judgment about
whether two names mean the same thing goes to /admin for him to decide).

TWO TIERS, DELIBERATELY DIFFERENT MECHANISMS, NOT JUST DIFFERENT
CONFIDENCE THRESHOLDS ON THE SAME CHECK
---------------------------------------
Tier 1 (auto-merge) is PURELY MECHANICAL -- no model call, no judgment,
just stripping words that describe how something was CUT or DESCRIBED,
never what it IS. "loosely packed fresh mint leaves" and "mint leaves"
normalize to the same key; "sugar" and "brown sugar" never can, because
"brown" is not in the filler list -- Jason's own example (sugar/water
this session, cold water = water but brown sugar =/= sugar) is the
calibration this list was built against. Two names sharing a normalized
key are, by construction, the same thing described with different
amounts of cutting/prep detail, which is what makes auto-merge safe here
specifically -- there is no step where a MEANING judgment gets made
silently.

Tier 2 (review queue) is where real judgment starts, so a model gets
involved -- but ANYTHING it proposes lands in merge_proposals for
Jason's own approval, unconditionally, regardless of how confident the
model claims to be. A model being sure is not the same as a model being
right, and this whole feature exists downstream of a session that
already found three separate cases of an LLM/search engine being
confidently wrong (mint matched to infant formula, ice cream matched to
"ice", real rum matched to "nonalcoholic rum" -- see nutrition.py). No
reason to trust identity judgments any further than magnitude judgments
were trusted.

MERGING GOES THROUGH TANDOOR'S OWN REAL MERGE ENDPOINT, NOT HAND-ROLLED
-------------------------------------------------------------------------
Confirmed live by reading Tandoor's actual view (cookbook/views/api.py's
MergeMixin.merge): `PUT /api/food/{id}/merge/{target}/` reassigns EVERY
foreign-key reference (every recipe's ingredient rows, tree children,
the works) from the source onto the target, then deletes the source --
atomic, on Tandoor's own side. No reason to reimplement that by hand.
"""
from . import britishisms, core, foodmatch, llm, store, tandoor, vram

# The mechanical normalize/core-word helpers now live in foodmatch.py,
# shared with tandoor.py's own live on-import matching (see that module's
# own docstring on why -- tandoor.py cannot import this file, since this
# file already imports tandoor.py). Called directly as foodmatch.normalize
# (name, kind)/foodmatch.core_word(name, kind) below, not aliased -- every
# call site needs its own `kind` ("food" keeps the full filler list,
# "tool" keeps size words significant, see foodmatch._TOOL_KEEP), and a
# bare alias would have no way to carry that through.


GROUP_SYSTEM = """You group kitchen ingredient or tool names that mean the
SAME real-world thing, described differently -- never things that are
actually different, even if related.

Calibration, exact: "cold water" and "water" are the same thing. "brown
sugar" and "sugar" are DIFFERENT things -- brown sugar is not a kind of
phrasing for sugar, it is a different product with different moisture and
flavor. When in doubt, they are different. A merge you should NOT propose
is far cheaper than one you should not have.

Only propose a merge when the difference between two names is pure
phrasing/scraping noise (plural, capitalization, a stray descriptor that
doesn't change the product) -- never propose a merge across a difference
in: color, variety, brand, cooked-vs-raw, fresh-vs-dried, alcoholic-vs-
non-alcoholic, or any qualifier that changes what a shopper would actually
buy.

Return JSON: {"groups": [{"canonical": "<the cleanest of the names, verbatim>",
"members": ["<other name from the list, verbatim>", ...],
"reason": "<one short sentence>"}, ...]}
Every name in "members" must be copied EXACTLY from the input list. Omit
any name you are not proposing to merge -- do not force everything into a
group. If nothing in the list should merge, return {"groups": []}."""


async def _ask_group(names: list) -> list:
    """One call, one bucket. Returns a list of {canonical, members, reason}
    -- see GROUP_SYSTEM for why NOTHING here is trusted enough to skip the
    review queue."""
    if len(names) < 2:
        return []
    user = "Names:\n" + "\n".join(f"- {n}" for n in names)
    try:
        async with vram.parsing():
            data = await llm.chat_json(GROUP_SYSTEM, user, model=core.LLM_READ,
                                       temperature=0.1, max_tokens=1000)
    except llm.LLMError:
        return []
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return []
    by_name = {n.strip().lower(): n for n in names}
    out = []
    for g in groups:
        canonical = (g.get("canonical") or "").strip()
        members = [m.strip() for m in (g.get("members") or []) if m.strip()]
        reason = (g.get("reason") or "").strip()
        if canonical.lower() not in by_name:
            continue
        real_members = [by_name[m.lower()] for m in members if m.lower() in by_name]
        real_members = [m for m in real_members if m.lower() != canonical.lower()]
        if real_members:
            out.append({"canonical": by_name[canonical.lower()],
                        "members": real_members, "reason": reason or "Same item, different phrasing."})
    return out


async def _tier1_merge(items: list, merge_fn, dry_run: bool, kind: str = "food") -> tuple:
    """Exact-normalized-key groups, no model, auto-merge -- pulled out of
    run() so a quick safety check can call just this half without paying
    for the slow, model-assisted tier 2 below it. Returns (merged_ids,
    tier1_merges, sample) -- sample is every merge actually made/would-be-
    made, for a caller that wants to show what happened, not just a count.
    `kind="tool"` keeps size words significant (foodmatch._TOOL_KEEP) --
    a "large pot" and a "small pot" never normalize to the same key."""
    by_key: dict = {}
    for it in items:
        key = foodmatch.normalize(it["name"], kind)
        if len(key) < 3:
            continue
        by_key.setdefault(key, []).append(it)

    merged_ids = set()
    tier1_merges = 0
    sample = []
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        # Canonical = whichever member's name already IS its own
        # normalized key (no filler words at all), else the shortest
        # WELL-FORMED name. CAUGHT LIVE (2026-08-25): "shortest wins" on
        # its own picked "chives (for" -- a genuinely broken, truncated
        # name already sitting in the library (unbalanced paren, cut off
        # mid-word) -- over "Chives, for garnish" for exactly the reason
        # it looked attractive to the heuristic: it's short BECAUSE it's
        # cut off. Balanced-parens candidates are preferred first, same
        # structural check tandoor._food_name_looks_clean() already uses
        # for the same reason; only fall through to every candidate when
        # none of them qualify (nothing well-formed to prefer instead).
        canonical = next((m for m in group if m["name"].strip().lower() == key), None)
        if canonical is None:
            well_formed = [m for m in group if m["name"].count("(") == m["name"].count(")")]
            canonical = min(well_formed or group, key=lambda m: len(m["name"]))
        for m in group:
            if m["id"] == canonical["id"]:
                continue
            merged_ids.add(m["id"])
            if dry_run:
                tier1_merges += 1
                sample.append({"from": m["name"], "to": canonical["name"]})
                continue
            ok = await merge_fn(m["id"], canonical["id"])
            if ok:
                tier1_merges += 1
                sample.append({"from": m["name"], "to": canonical["name"]})
    return merged_ids, tier1_merges, sample


async def tier1_only(kind: str, dry_run: bool = False) -> dict:
    """The fast, mechanical half of run() on its own -- Jason's own
    request (2026-08-25), after tidy_names() renaming "firmly brown
    sugar" -> "brown sugar" created a fresh, un-caught collision with an
    existing "Brown Sugar": "finish out tonight's food cleanup... catch
    any fresh case-duplicate collisions the renames tonight might have
    created elsewhere." A rename can create a NEW exact-key collision
    that didn't exist before it ran, and nothing re-checks for that on
    its own -- this is the cheap way to sweep for it without paying for
    another full tier-2 model pass (which can run 30-60 minutes)."""
    items = await (tandoor.all_foods() if kind == "food" else tandoor.all_tools())
    items = [{"id": it["id"], "name": it.get("name") or ""} for it in items if it.get("name")]
    merge_fn = tandoor.merge_food if kind == "food" else tandoor.merge_tool
    _, merges, sample = await _tier1_merge(items, merge_fn, dry_run, kind)
    return {"kind": kind, "total": len(items), "tier1_merged": merges, "sample": sample}


async def run(kind: str, dry_run: bool = False) -> dict:
    """kind is "food" or "tool". Returns a tally: tier-1 auto-merges done,
    tier-2 proposals queued, groups considered. dry_run skips the actual
    Tandoor merge calls and store writes -- for checking what WOULD happen
    before trusting a fresh change to this module against real data."""
    items = await (tandoor.all_foods() if kind == "food" else tandoor.all_tools())
    items = [{"id": it["id"], "name": it.get("name") or ""} for it in items if it.get("name")]
    merge_fn = tandoor.merge_food if kind == "food" else tandoor.merge_tool

    merged_ids, tier1_merges, _ = await _tier1_merge(items, merge_fn, dry_run, kind)

    # --- tier 2: broader buckets by core word, model-assisted, always queued
    # CAUGHT LIVE (2026-08-25), Jason's own report that /llm-findings
    # "doesn't update its findings": merge_proposal_add() is a bare
    # INSERT with no uniqueness check, so re-running this on an item that
    # already has a proposal sitting unreviewed just added an exact
    # duplicate row -- wasted work (a whole model call, re-deciding the
    # same thing) and duplicate clutter on the review page, both making
    # a fresh run look like it changed nothing even when it legitimately
    # found new stuff elsewhere. An item with a pending proposal is
    # skipped here entirely -- once Jason approves or rejects it, it's
    # naturally eligible again next run.
    already_pending = {p["item_id"] for p in store.merge_proposals() if p["kind"] == kind}
    remaining = [it for it in items if it["id"] not in merged_ids and it["id"] not in already_pending]
    by_core: dict = {}
    for it in remaining:
        core_word = foodmatch.core_word(it["name"], kind)
        if not core_word:
            continue
        by_core.setdefault(core_word, []).append(it)

    proposals_queued = 0
    groups_considered = 0
    # CAUGHT LIVE (2026-08-25), a real dedupe run that never finished in
    # reasonable time: _ask_group() opens its own vram.parsing() per
    # bucket, and with no OUTER context spanning the loop, every single
    # bucket was its own "outermost" call -- evicting and reloading
    # Phi-4 (a 14B model) on EVERY bucket instead of once for the whole
    # pass. Same exact bug, same fix, as the one already caught and
    # fixed in the old batch reparse script (see reparse.py's own
    # history) -- one vram.parsing() here makes every _ask_group() call
    # below a no-op nested re-entry instead of a full reload.
    async with vram.parsing():
        for core_word, group in by_core.items():
            # Already-identical-after-tier-1-normalization members would
            # have merged above; a bucket only reaches here with GENUINE
            # spelling/wording differences left, which is exactly what
            # needs a model's read rather than another mechanical pass.
            distinct_keys = {foodmatch.normalize(m["name"], kind) for m in group}
            if len(distinct_keys) < 2:
                continue
            groups_considered += 1
            names = [m["name"] for m in group]
            by_name = {m["name"]: m for m in group}
            for proposal in await _ask_group(names):
                canonical_item = by_name.get(proposal["canonical"])
                if not canonical_item:
                    continue
                for member_name in proposal["members"]:
                    member_item = by_name.get(member_name)
                    if not member_item or member_item["id"] == canonical_item["id"]:
                        continue
                    if dry_run:
                        proposals_queued += 1
                        continue
                    store.merge_proposal_add(
                        kind, member_item["id"], member_item["name"],
                        canonical_item["id"], canonical_item["name"], proposal["reason"])
                    proposals_queued += 1

    return {"kind": kind, "total": len(items), "tier1_merged": tier1_merges,
           "tier2_queued": proposals_queued, "tier2_groups_considered": groups_considered}


async def tidy_names(kind: str, dry_run: bool = False) -> dict:
    """RENAME every food/tool to its own cleaned identity -- Jason's own
    request (2026-08-25), browsing /admin/items: "(60ml) lemon juice
    from 1 lemon" and "A drizzle of lemon juice" are the same food,
    "lemon juice", with a portion size baked into the FOOD'S name
    instead of left in the recipe's own ingredient line where it
    belongs -- "I don't want to change the portions". Nothing here
    touches a recipe's own ingredient text (Tandoor's Food is a shared
    row referenced by id, not copied text -- same guarantee
    tandoor.rename_food() already relies on).

    DIFFERENT FROM run() ABOVE ON PURPOSE: run()'s Tier 1 only acts
    when two EXISTING items happen to collide after normalizing --
    "A drizzle of lemon juice" sitting there ALONE, with no duplicate
    to merge into, was never touched by it. This renames every item to
    its own clean form regardless of whether a duplicate exists, and
    only merges when that clean form turns out to already belong to
    a DIFFERENT existing item. Still mechanical, still no model call --
    foodmatch.clean_name() is a fixed word list, same safety argument
    as run()'s own Tier 1: "coarse sea salt" has no filler word in it
    describing a real variety away, so it is never touched; "brown
    sugar" keeps "brown" for the same reason.

    kind="tool" WORKS (kept size words safe via foodmatch's kind param)
    BUT ISN'T RECOMMENDED -- CAUGHT LIVE (2026-08-25), actually running
    it: "bowl of ice water" -> "bowl ice water", "jar with a tight-
    fitting lid" -> "jar with tight-fitting lid". Neither merged into
    anything -- there was no duplicate to consolidate, just a stray
    connective stripped out of an otherwise-intact descriptive phrase,
    making it read worse for zero deduplication benefit. Food names are
    mostly bare identities ("brown sugar") where stripping filler
    reveals the real product; tool names are more often short
    descriptions of a specific setup, where the filler word is part of
    normal English rather than noise. tier1_only(), which only ever
    ACTS when a real collision exists, is the safe one for tools --
    prefer it over this for that kind."""
    items = await (tandoor.all_foods() if kind == "food" else tandoor.all_tools())
    items = [{"id": it["id"], "name": it.get("name") or ""} for it in items if it.get("name")]
    # CAUGHT LIVE (2026-08-25): a plain {key: item} comprehension is
    # last-write-wins, so when TWO existing items already collide after
    # normalizing (a case variant like "Brown Sugar" next to an
    # as-yet-uncleaned "firmly brown sugar", both -> "brown sugar"), the
    # messy one could win the slot and point BACK AT ITSELF -- then
    # processing that same messy item below sees "existing is me", takes
    # the rename branch instead of merge, and the two end up as
    # "Brown Sugar" and "brown sugar" side by side instead of one. Same
    # canonical-picking rule as run()'s own Tier 1 above (prefer a name
    # that's already its own normalized key, else the shortest) fixes it
    # by construction -- the slot can never point at a name that still
    # needs cleaning while a cleaner alternative already exists.
    by_key: dict = {}
    _groups: dict = {}
    for it in items:
        _groups.setdefault(foodmatch.normalize(it["name"], kind), []).append(it)
    for key, group in _groups.items():
        canonical = next((m for m in group if m["name"].strip().lower() == key), None)
        if canonical is None:
            # Same "don't pick a broken name just because it's short" rule
            # as _tier1_merge()'s own canonical selection above.
            well_formed = [m for m in group if m["name"].count("(") == m["name"].count(")")]
            canonical = min(well_formed or group, key=lambda m: len(m["name"]))
        by_key[key] = canonical
    rename_fn = tandoor.rename_food if kind == "food" else tandoor.rename_tool
    merge_fn = tandoor.merge_food if kind == "food" else tandoor.merge_tool

    renamed, merged, skipped = 0, 0, 0
    sample = []
    for it in items:
        # britishisms BEFORE clean_name() -- CAUGHT LIVE (2026-08-25):
        # this pass never called it at all, so "clear honey" and "smoked
        # streaky bacon" sat unrenamed through repeated tidy_names()
        # runs even after _clean_parsed_food_names() started catching
        # BOTH at import time (see that function's own docstring) --
        # this is the retroactive half, for names created before that
        # existed. Both dictionaries are pure word/phrase lists (no
        # kind-specific risk the way size words are for foodmatch's own
        # filler set), safe for tools too -- "tea towel"/"muslin" are
        # real entries in britishisms.py's own list.
        clean = britishisms.americanize_name(it["name"])
        clean = foodmatch.clean_name(clean, kind)
        if not clean or clean.strip().lower() == it["name"].strip().lower():
            continue
        existing = by_key.get(foodmatch.normalize(clean, kind))
        if existing and existing["id"] != it["id"]:
            if dry_run:
                merged += 1
                sample.append({"from": it["name"], "to": existing["name"], "how": "merged"})
                continue
            if await merge_fn(it["id"], existing["id"]):
                merged += 1
                sample.append({"from": it["name"], "to": existing["name"], "how": "merged"})
            else:
                # CAUGHT LIVE: this used to fall through uncounted -- a
                # failed merge attempt (Tandoor's own API rejecting it)
                # was neither retried nor visible in the tally, so a
                # collision like this just silently stayed unresolved
                # forever with nothing in the returned counts to explain
                # why.
                skipped += 1
            continue
        if dry_run:
            renamed += 1
            sample.append({"from": it["name"], "to": clean, "how": "renamed"})
            continue
        if await rename_fn(it["id"], clean):
            renamed += 1
            sample.append({"from": it["name"], "to": clean, "how": "renamed"})
            by_key[foodmatch.normalize(clean, kind)] = it
        else:
            skipped += 1

    return {"kind": kind, "total": len(items), "renamed": renamed,
           "merged": merged, "skipped": skipped, "sample": sample[:30]}
