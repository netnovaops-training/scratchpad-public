"""Tests for the parts that must be exact.

RUN THEM:
    docker compose -f docker-compose.yml -f docker-compose.web.yml `
      run --rm --entrypoint python kitchen -m pytest -q test_kitchen.py

or without pytest installed:
    docker compose ... run --rm --entrypoint python kitchen test_kitchen.py

WHAT IS TESTED AND WHAT IS NOT
------------------------------
Everything here is deterministic: scaling, fraction rendering, JSON-LD
extraction, step numbering, image matching, diagram scene choice. That is not
an accident of what was easy to test -- it is the same list as "the things this
app refuses to ask a model", and it is testable precisely because it is code.

Nothing here calls litellm or Mealie. A test that needs the GPU is a test that
does not run, and a suite you skip is worse than no suite because it looks like
coverage.
"""
import json
import pathlib
import sys
from fractions import Fraction

sys.path.insert(0, "/app")

from app import (art, basket, crawl, diagram, ing_clean, importer, labels,  # noqa: E402
                 scale, subs, taxonomy)

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}\n     got  {got!r}\n     want {want!r}")


# ---------------------------------------------------------------------------
#  Scaling
# ---------------------------------------------------------------------------
def test_scaling():
    two, half = Fraction(2), Fraction(1, 2)
    check("double 1 1/2", scale.line("1 1/2 cups flour", two)[0], "3 cups flour")
    check("halve 3 eggs", scale.line("3 eggs", half)[0], "1 1/2 eggs")
    check("double vulgar", scale.line("½ tsp salt", two)[0], "1 tsp salt")
    check("double range", scale.line("2-3 cloves garlic", two)[0], "4-6 cloves garlic")
    check("double decimal", scale.line("1.5 kg beef", two)[0], "3 kg beef")
    check("double word", scale.line("two large eggs", two)[0], "4 large eggs")
    check("to-range", scale.line("1 to 2 tbsp honey", two)[0], "2-4 tbsp honey")

    # The important negatives: a quantity that is not a number must not be
    # invented into one.
    check("pinch untouched", scale.line("a pinch of salt", two)[0], "a pinch of salt")
    check("oil untouched", scale.line("olive oil, for frying", two)[0],
          "olive oil, for frying")
    check("pinch not scaled", scale.line("a pinch of salt", two)[1], False)

    # x1 must be a no-op, not a re-render. Otherwise opening a recipe at its
    # own serving count silently rewrites every line it was written with.
    check("identity", scale.line("1 1/2 cups flour", Fraction(1))[0],
          "1 1/2 cups flour")


def test_counts_round_differently():
    """The lasagne test: 6 servings scaled to 8 is x1.375.

    On measurements that is 1 3/8 tbsp, which is fine. On "1 onion" it used to
    produce "1 3/8 onion", which is the kind of output that makes someone stop
    trusting the whole page.
    """
    f = Fraction(4, 3)
    check("onion halves", scale.line("1 onion, finely chopped", f)[0],
          "1 1/2 onion, finely chopped")
    check("rashers", scale.line("2 rashers smoked streaky bacon", f)[0],
          "2 1/2 rashers smoked streaky bacon")
    # "2 garlic cloves" -- the word after the number is "garlic", not a unit,
    # so this rounds as a count. That is the better answer anyway: half a
    # clove of garlic is a thing; five eighths of one is not.
    check("garlic cloves count", scale.line("2 garlic cloves, finely chopped", f)[0],
          "2 1/2 garlic cloves, finely chopped")
    # But when the unit comes first it IS a measurement.
    check("cloves as a unit", scale.line("2 cloves of garlic", f)[0],
          "2 5/8 cloves of garlic")
    # A vague unit after a bare "a" is not a quantity at all.
    check("a pinch", scale.line("a pinch of salt", Fraction(2))[0],
          "a pinch of salt")
    check("a cup is", scale.line("a cup of sugar", Fraction(2))[0],
          "2 cup of sugar")
    # A measurement keeps its eighths -- this is the case halves would ruin.
    check("tbsp keeps eighths", scale.line("1 tbsp olive oil", f)[0],
          "1 3/8 tbsp olive oil")
    # A unit stuck to its number with no space. This is most of a British
    # ingredient list, and it silently did not scale at all until the `rest`
    # group in _LEAD learned to end on a letter.
    check("no space before unit", scale.line("500g beef mince", Fraction(2))[0],
          "1000g beef mince")
    check("ml too", scale.line("400ml crème fraîche", Fraction(1, 2))[0],
          "200ml crème fraîche")
    check("still a measurement", scale.line("125g ball mozzarella", f)[0],
          "167g ball mozzarella")
    # Four or more of a countable thing is a whole number: nobody halves the
    # fifth rasher.
    check("big counts are whole", scale.line("4 onions", f)[0], "5 onions")
    # "g" must not match the g of "garlic", or a count is read as a weight.
    check("garlic is not grams", scale.line("1 garlic clove", f)[0],
          "1 1/2 garlic clove")


def test_render():
    check("eighths", scale.render(Fraction(1, 3)), "3/8")
    check("whole", scale.render(Fraction(4)), "4")
    check("mixed", scale.render(Fraction(9, 4)), "2 1/4")
    # Never round a real quantity away to nothing -- 1/8 tsp is a real amount,
    # "0 tsp" is a missing ingredient.
    check("floor is an eighth", scale.render(Fraction(1, 40)), "1/8")
    # Above 10 there is no measuring spoon, so no fractions.
    check("big is whole", scale.render(Fraction(151, 4)), "38")


def test_servings():
    check("plain", scale.servings_in("4 servings"), 4)
    check("range low end", scale.servings_in("Serves 4-6"), 4)
    check("makes", scale.servings_in("Makes 12 cookies"), 12)
    check("none", scale.servings_in(""), None)
    check("prose", scale.servings_in("a generous amount"), None)
    check("factor", scale.factor_for("Serves 4", 6), (Fraction(3, 2), 4))
    # No stated yield means nothing to scale FROM. Must be a no-op, not a guess.
    check("unknown yield", scale.factor_for("", 6), (Fraction(1), None))


# ---------------------------------------------------------------------------
#  Reading a page
# ---------------------------------------------------------------------------
PAGE = """<html><head>
<meta property="og:image" content="/img/hero.jpg">
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
  {"@type":"WebPage","name":"blog"},
  {"@type":"Recipe","name":"Leek and Potato Soup",
   "image":["https://ex.test/soup.jpg"],
   "recipeYield":"Serves 4",
   "prepTime":"PT15M","cookTime":"PT1H30M",
   "recipeIngredient":["2 leeks, sliced","500g potatoes","1 litre stock"],
   "recipeInstructions":[
     {"@type":"HowToStep","text":"Sweat the leeks in butter for 10 minutes."},
     {"@type":"HowToStep","text":"Add the potatoes and stock and simmer."},
     {"@type":"HowToStep","text":"Blend until smooth and serve."}],
   "description":"A simple soup."}]}
</script></head><body>
<img src="/img/logo.png" alt="site logo">
<img src="/img/step1.jpg" alt="Sweating the sliced leeks in a knob of butter">
<p>Grandmother story goes here.</p>
</body></html>"""


def test_jsonld():
    reader = crawl.read(PAGE)
    r = crawl.from_jsonld(reader, "https://ex.test/soup")
    check("title", r["title"], "Leek and Potato Soup")
    check("yields", r["yields"], "Serves 4")
    check("ingredients", len(r["ingredients"]), 3)
    check("steps", len(r["steps"]), 3)
    check("prep", r["prep"], "15 min")
    check("cook", r["cook"], "1 hr 30 min")
    check("image", r["image"], "https://ex.test/soup.jpg")
    check("how", r["how"], "read straight from the page's own recipe data")


def test_images():
    reader = crawl.read(PAGE)
    imgs = crawl.harvest_images(reader, "https://ex.test/soup")
    srcs = [i["src"] for i in imgs]
    check("logo dropped", any("logo" in s for s in srcs), False)
    check("step kept", "https://ex.test/img/step1.jpg" in srcs, True)


def test_drop_hero():
    imgs = [{"src": "https://ex.test/a.jpg?resize=440,400", "alt": "hero"},
            {"src": "https://ex.test/b.jpg", "alt": "step"}]
    # Same file, different query string. A plain string compare misses this,
    # and then the finished dish gets captioned as a middle step.
    out = importer.drop_hero(imgs, "https://ex.test/a.jpg?quality=90")
    check("hero gone", [i["src"] for i in out], ["https://ex.test/b.jpg"])
    check("no hero, no change", len(importer.drop_hero(imgs, "")), 2)


def test_basket_words():
    """An ingredient line reduced to the thing it actually names."""
    check("strips amount and prep",
          basket.key_words("3/4 cup double cream, cold"),
          {"double", "cream", "cold"})
    check("garlic is just garlic",
          basket.key_words("2 cloves of garlic, finely minced"), {"garlic"})
    check("plural collapses",
          basket.key_words("tomatoes"), basket.key_words("tomato"))
    check("staple", basket.is_staple("a pinch of salt"), True)
    check("not a staple", basket.is_staple("500g chicken thighs"), False)


def test_basket_matching():
    """A false match is worse than no match -- it says dinner is possible."""
    check("same thing",
          basket.same_thing("500g chicken thighs", "chicken thighs"), True)
    check("shorter inside longer",
          basket.same_thing("chicken", "2 chicken thighs, skin on"), True)
    # THE ONE THAT MATTERS. One word in common is not a match: it would make
    # "double cream" satisfy "cream of tartar", and somebody would find out
    # halfway through cooking.
    check("cream is not cream of tartar",
          basket.same_thing("double cream", "cream of tartar"), False)
    check("not soured cream",
          basket.same_thing("double cream", "soured cream"), False)
    check("empty never matches", basket.same_thing("", "chicken"), False)


def test_basket_could_make():
    recipes = [
        {"id": "a", "title": "Chicken fajitas",
         "ingredients": ["500g chicken thighs", "2 peppers", "1 lime",
                         "salt", "olive oil"]},
        {"id": "b", "title": "Beef stew",
         "ingredients": ["1kg beef shin", "3 carrots", "500ml stock"]},
        {"id": "c", "title": "Toast", "ingredients": ["bread"]},
    ]
    out = basket.could_make(["chicken thighs", "peppers"], recipes)
    check("fajitas suggested", out[0]["id"], "a")
    # Staples are ignored on BOTH sides, so the fajitas count 3 real
    # ingredients rather than 5 -- otherwise nothing ever looks close.
    check("staples not counted", out[0]["total"], 3)
    check("two of three", out[0]["got"], 2)
    check("says what is missing", out[0]["missing"], ["1 lime"])
    # A one-ingredient "recipe" is a bad import, not a suggestion.
    check("no single-ingredient", any(o["id"] == "c" for o in out), False)
    # Nothing in the trolley means nothing to suggest, not everything.
    check("empty basket", basket.could_make([], recipes), [])
    check("only staples", basket.could_make(["salt", "water"], recipes), [])


def test_basket_grouping():
    items = [
        {"draft_id": "x", "source": "Lasagne", "done": 1, "text": "beef"},
        {"draft_id": "x", "source": "Lasagne", "done": 0, "text": "pasta"},
        {"draft_id": "", "source": "", "done": 0, "text": "bin bags"},
    ]
    g = basket.by_recipe(items)
    check("two groups", len(g), 2)
    check("recipe first", g[0]["title"], "Lasagne")
    check("counts", (g[0]["got"], g[0]["needed"]), (1, 1))
    # Hand-added lines are named, not hidden and not folded into a recipe.
    check("hand-added named", g[1]["title"], "Added by hand")


def test_added_by_pill():
    """Who put it here -- separate from who cooked it."""
    check("mama", labels.added_by("person3@example.com"), "mama's finds")
    check("casey", labels.added_by("person2@example.com"), "casey's finds")
    check("jason", labels.added_by("person4@example.com"), "jason's finds")
    check("unknown", labels.added_by("person5@example.com"), "auntjune's finds")
    check("nobody", labels.added_by(""), "")
    # The two kinds must stay distinguishable, or the valuable one is diluted.
    check("not a made pill", labels.is_made_pill("casey's finds"), False)
    check("is an added pill", labels.is_added_pill("casey's finds"), True)
    check("made is not added",
          labels.is_added_pill("casey made and tested"), False)


def test_labels_tidy():
    """The prompt asks for a shape; this is what enforces it."""
    check("lowercased", labels.tidy(["CHICKEN"]), ["chicken"])
    check("junk dropped", labels.tidy(["delicious", "recipe", "tasty"]), [])
    check("punctuation", labels.tidy(["one-pot!", "  soup  "]), ["one-pot", "soup"])
    check("deduped", labels.tidy(["soup", "Soup"]), ["soup"])
    # A four-word "label" is a description. Trimmed to two rather than dropped
    # -- and trimmed BEFORE the length check, or a long one is thrown away
    # before the trim that would have saved it.
    check("trimmed not dropped",
          labels.tidy(["quick weeknight chicken dinner"]), ["quick weeknight"])
    check("capped at five",
          len(labels.tidy(["a1", "b2", "c3", "d4", "e5", "f6", "g7"])), 5)


def test_labels_snap():
    """What stops the browse page growing a pill per recipe."""
    vocab = [("chicken", 9), ("quick", 6), ("brownie", 3), ("soup", 2)]
    check("plural", labels.snap(["chickens"], vocab), ["chicken"])
    # "brownies" is the case a single-answer stemmer gets wrong: an
    # "ies -> y" rule turns it into "browny", which matches nothing.
    check("brownies", labels.snap(["brownies"], vocab), ["brownie"])
    check("contains", labels.snap(["chicken thighs"], vocab), ["chicken"])
    check("es plural", labels.snap(["soups"], vocab), ["soup"])
    # Genuinely new stays new -- "invent freely" has to still mean something.
    check("new stays new", labels.snap(["pear"], vocab), ["pear"])
    # A word boundary is required, or "pear" snaps to "spear".
    check("no substring", labels.snap(["hamburger"], [("ham", 4)]), ["hamburger"])
    check("no duplicates",
          labels.snap(["chicken", "chickens"], vocab), ["chicken"])


def test_course():
    """A closed set. Every way of saying a course lands on one word."""
    check("plain", taxonomy.clean_course("dinner"), "dinner")
    check("main is dinner", taxonomy.clean_course("main"), "dinner")
    # "entree" means the main course in America and the starter in France.
    # Mapped rather than stemmed, because no amount of stemming decides that.
    check("entree", taxonomy.clean_course("Entree"), "dinner")
    check("starter", taxonomy.clean_course("starter"), "appetizer")
    check("plural", taxonomy.clean_course("sides"), "side")
    check("british pudding", taxonomy.clean_course("pudding"), "dessert")
    check("cornbread is bread", taxonomy.clean_course("cornbread"), "bread")
    # REFUSES rather than guessing. An empty course reads as "nobody said";
    # a wrong one reads as fact.
    check("unknown refused", taxonomy.clean_course("elevenses"), "")
    check("empty", taxonomy.clean_course(""), "")


def test_cuisine():
    """Open, unlike course — the list is a starting point, not an authority."""
    check("known", taxonomy.clean_cuisine("Soul Food"), "soul food")
    check("hyphen", taxonomy.clean_cuisine("tex mex"), "tex-mex")
    check("case and space", taxonomy.clean_cuisine("  CREOLE "), "creole")
    # The important one: a tradition this list does not name is the list's
    # failing, not the cook's. Keep what they typed.
    check("keeps the unlisted",
          taxonomy.clean_cuisine("Sichuan"), "sichuan")
    # The apostrophe is KEPT. A family's own word for how they cook is a
    # perfectly good answer to "where is this from", and stripping the
    # punctuation out of "Big Mama's" would be tidying away the name.
    check("keeps a family word",
          taxonomy.clean_cuisine("Big Mama's"), "big mama's")
    check("too long dropped", taxonomy.clean_cuisine("x" * 40), "")
    check("empty", taxonomy.clean_cuisine(""), "")

    # These traditions are listed separately from "american" on purpose, and
    # this test is here so nobody "tidies" them into it later.
    for name in ("soul food", "southern", "creole", "cajun", "gullah geechee"):
        check(f"{name} is its own", name in taxonomy.CUISINES, True)


def test_taxonomy_locked():
    """A person's answer is not re-guessed."""
    check("fresh import", taxonomy.locked({"cuisine": "american"}), False)
    check("set by hand",
          taxonomy.locked({"cuisine": "soul food", "taxonomy_by_hand": True}), True)


def test_made_pills():
    """The pills only a person can give — one per cook."""
    check("known cook", labels.made_by("person3@example.com"), "mama made and tested")
    check("casey", labels.made_by("person2@example.com"), "casey made and tested")
    check("owner", labels.made_by("person1@example.com"), "jason made and tested")
    # Somebody added at /admin/people who is not in the map has still cooked
    # the thing. A pill from their address beats no pill.
    check("unknown falls back",
          labels.made_by("person5@example.com"), "auntjune made and tested")
    check("recognised", labels.is_made_pill("casey made and tested"), True)
    check("not a made pill", labels.is_made_pill("chicken"), False)
    # tidy() must not shorten these to two words — "mama made and tested" is
    # four, and trimming it would break every one of them.
    check("survives tidy",
          labels.protected(["chicken", "mama made and tested"]),
          ["mama made and tested"])


def test_seeded_vocabulary():
    """Seeds decide which of several equally-good words wins."""
    vocab = labels.vocabulary([("chicken", 3)])
    check("used comes first", vocab[0], ("chicken", 3))
    check("seeds join at zero", ("slow-cooker", 0) in vocab, True)
    check("no duplicate of a used word",
          [n for n, _ in vocab].count("chicken"), 1)
    # The point of seeding: two ways of saying "crockpot" land on one pill.
    for said in ("crock pot", "crockpots", "crockpot"):
        check(f"snaps {said}", labels.snap([said], vocab), ["slow-cooker"])
    check("30 minutes", labels.snap(["30 minutes"], vocab), ["30 minutes"])
    check("no-bake and no bake land on the same pill",
         labels.snap(["no-bake"], vocab), ["no bake"])
    check("blender is seeded", ("blender", 0) in vocab, True)
    # "crockpot" shares no word with "slow-cooker", so squashing and
    # stemming alone never join them -- this is what ALIAS is for.
    for said in ("crockpot", "crock-pot", "crockpots"):
        check(f"{said!r} joins the slow-cooker pill via ALIAS",
             labels.snap([said], vocab), ["slow-cooker"])
    # CAUGHT LIVE, the harder case (with the ORIGINAL crockpot->slow-cooker
    # direction, before Jason asked for it to flip -- the mechanism this
    # proves still matters either way): an alias has to beat "this is
    # already a known pill", not just fill a gap when there is no
    # competing pill yet, or a spelling already in wide use before an
    # alias existed would never actually merge.
    already_established = labels.vocabulary([("crockpot", 12), ("chicken", 3)])
    check("crockpot already has 12 recipes on it",
         ("crockpot", 12) in already_established, True)
    check("ALIAS still wins over an already-established competing spelling",
         labels.snap(["crockpot"], already_established), ["slow-cooker"])


def test_labels_dietary():
    """The one class of label where being usually-right is not good enough.

    The model actually produced ["chicken", "fajitas", "easy", "vegetarian"]
    for a chicken recipe on this machine -- wrong, and self-contradicting in
    its own four words.
    """
    chicken = {"ingredients": ["500g chicken thighs", "2 peppers", "1 lime"]}
    check("meat kills vegetarian",
          labels.verify_dietary(["chicken", "vegetarian"], chicken), ["chicken"])
    check("fish counts as meat",
          labels.verify_dietary(["vegetarian"], {"ingredients": ["2 anchovies"]}), [])
    check("butter kills vegan",
          labels.verify_dietary(["vegan"], {"ingredients": ["100g butter"]}), [])
    check("butter kills dairy-free",
          labels.verify_dietary(["dairy-free"], {"ingredients": ["100g butter"]}), [])
    check("flour kills gluten-free",
          labels.verify_dietary(["gluten-free"], {"ingredients": ["225g flour"]}), [])

    # A genuine claim survives.
    beans = {"ingredients": ["1 tin black beans", "2 peppers", "olive oil"]}
    check("real vegan survives",
          labels.verify_dietary(["vegan", "quick"], beans), ["vegan", "quick"])
    # It only ever REMOVES. Absence of a meat word is not evidence of anything.
    check("never adds", labels.verify_dietary(["quick"], beans), ["quick"])
    # Nothing to check against means nothing is removed.
    check("no ingredients, no opinion",
          labels.verify_dietary(["vegan"], {"ingredients": []}), ["vegan"])


def test_denumber():
    check("digit dot", importer._denumber("1. Preheat the oven"), "Preheat the oven")
    check("step word", importer._denumber("Step 3: Add flour"), "Add flour")
    check("left alone", importer._denumber("Preheat the oven"), "Preheat the oven")
    # A quantity at the start of a step is NOT a step number. Stripping it
    # would delete the amount from "200g butter, melted" and nobody would spot
    # it until the cake failed.
    check("not a number", importer._denumber("200g butter, melted"),
          "200g butter, melted")


def test_attach_images():
    steps = ["Sweat the sliced leeks in butter", "Add stock", "Blend and serve"]
    images = [{"src": "a.jpg", "alt": "Sweating the sliced leeks in butter"}]
    out = importer.attach_images(steps, images)
    check("matched by alt", out[0]["photo"], "a.jpg")
    # Unmatched steps get a drawing, never a borrowed photo and never nothing.
    check("no borrowed photo", out[1]["photo"], "")
    check("drawing instead", out[1]["svg"].startswith("<svg"), True)

    # Counts differ -> positional matching must NOT fire, or the picture of
    # the finished dish ends up captioned "grease the tin".
    out2 = importer.attach_images(steps, [{"src": "x.jpg", "alt": ""},
                                          {"src": "y.jpg", "alt": ""}])
    check("no positional guess", [s["photo"] for s in out2], ["", "", ""])

    # Counts line up -> it is a step-by-step post, so position is meaningful.
    three = [{"src": f"{n}.jpg", "alt": ""} for n in "abc"]
    out3 = importer.attach_images(steps, three)
    check("positional", [s["photo"] for s in out3], ["a.jpg", "b.jpg", "c.jpg"])


def test_diagrams():
    check("oven", diagram.scene_for("Bake at 180C for 20 minutes"), "oven")
    check("tray not oven", diagram.scene_for("Arrange on a lined baking tray"), "tray")
    check("board", diagram.scene_for("Finely chop the garlic"), "board")
    check("boil", diagram.scene_for("Bring a pot of water to the boil"), "boil")
    check("temp label", diagram._temp_label("Bake at 180C"), "180°C")
    check("gas mark", diagram._temp_label("Bake at gas mark 4"), "gas 4")
    check("time label", diagram._time_label("Rest for 10 minutes"), "10 min")
    check("draws", diagram.svg_for("Chop the onion", 2).startswith("<svg"), True)


def test_subs_table():
    hit = subs.from_table("1 cup buttermilk, cold")
    check("found key", hit[0], "buttermilk")
    check("has ratio", "1 tbsp" in hit[1][0], True)
    check("miss", subs.from_table("2 star anise"), None)
    # Longest key first, or "self-raising flour" gets claimed by nothing and
    # a shorter key wins on a line that mentions both.
    check("longest first", subs.from_table("225g self-raising flour")[0],
          "self-raising flour")
    found = subs.scan(["3 eggs", "100g butter", "2 star anise"])
    check("scan count", len(found), 2)


def test_subs_equipment_table():
    hit = subs.from_equipment_table("I don't have a Dutch oven")
    check("found key", hit[0], "dutch oven")
    check("has a real workaround", "heavy" in hit[1][0].lower(), True)
    check("miss", subs.from_equipment_table("a sous vide machine"), None)
    # Longest key first -- "stand mixer with dough hook" must not be claimed
    # by the shorter "stand mixer" entry.
    check("longest first",
         subs.from_equipment_table("no stand mixer with dough hook")[0],
         "stand mixer with dough hook")


def test_subs_suggest_shows_the_table_and_the_model_together():
    """The table used to be skipped once the model was going to be asked
    anyway. Now both are asked every time and both are returned -- the
    table because it's proven, the model because it can speak to THIS
    dish, which no table entry can."""
    import asyncio
    from app import llm, subs

    async def fake_chat_json(system, user, **kw):
        return {"options": ["a splash of oil, one for one"], "note": "Leaner."}

    real = llm.chat_json
    llm.chat_json = fake_chat_json
    try:
        out = asyncio.run(subs.suggest("butter", dish="Shortbread"))
    finally:
        llm.chat_json = real
    check("the table's answer for butter is still there",
         "oil" in out["table"]["options"][0].lower(), True)
    check("the model's answer is there too, not instead of it",
         out["model"]["options"], ["a splash of oil, one for one"])
    check("no error recorded when the model actually answered",
         out["model_error"], "")

    async def fails(system, user, **kw):
        raise llm.LLMError("no route to the model")

    llm.chat_json = fails
    try:
        out = asyncio.run(subs.suggest("butter"))
    finally:
        llm.chat_json = real
    check("table survives on its own when the model fails",
         out["table"] is not None, True)
    check("model is None, not a crash, when it fails",
         out["model"], None)
    check("the failure is recorded so the page can say so",
         out["model_error"] != "", True)

    llm.chat_json = fails
    try:
        try:
            asyncio.run(subs.suggest("an ingredient nothing has ever heard of"))
            check("neither table nor model -> raises, not a silent blank", False, True)
        except llm.LLMError:
            pass
    finally:
        llm.chat_json = real


def test_subs_convert_exact_amount_for_a_named_substitute():
    """Different question from suggest(): not 'what could I use', but 'I
    have THIS specific thing, how much of it do I need' -- always the
    model (no table entry could know which brand of stevia is in the
    cupboard), given the recipe's own line WITH its quantity."""
    import asyncio
    from app import llm, subs

    async def fake_chat_json(system, user, **kw):
        check("the recipe's own quantity reaches the model, not just the name",
             "1 cup sugar" in user, True)
        check("the named substitute reaches the model",
             "stevia" in user, True)
        return {"amount": "1 teaspoon", "note": "Much sweeter by volume."}

    real = llm.chat_json
    llm.chat_json = fake_chat_json
    try:
        out = asyncio.run(subs.convert("1 cup sugar", "stevia", dish="Lemon bars"))
    finally:
        llm.chat_json = real
    check("amount carried through", out["amount"], "1 teaspoon")
    check("note carried through", out["note"], "Much sweeter by volume.")
    check("no allergy warning for an unrelated substitute", out["warning"], "")

    async def nut_answer(system, user, **kw):
        return {"amount": "1 cup", "note": "Will taste of almonds."}

    llm.chat_json = nut_answer
    try:
        out = asyncio.run(subs.convert("1 cup milk", "almond milk"))
    finally:
        llm.chat_json = real
    check("an allergen substitute still gets flagged, same as suggest()",
         out["warning"] != "", True)


def test_crawl_extracts_nutrition_rating_and_reviews_from_json_ld():
    """The gap this closes: a source page can publish its own nutrition
    facts and real reviews in the SAME structured Recipe block the
    ingredients and steps already come from, and none of it used to be
    read at all -- confirmed live on foodiecrush.com's chicken cacciatore,
    which publishes exactly this shape."""
    import json as _json

    reader = crawl.Reader.__new__(crawl.Reader)
    reader.ld = [_json.dumps({
        "@type": "Recipe", "name": "Test Bake",
        "recipeIngredient": ["1 cup flour"],
        "recipeInstructions": ["Bake."],
        "nutrition": {"calories": "205 kcal", "proteinContent": "27 g",
                     "servingSize": "1 serving"},
        "aggregateRating": {"ratingValue": "4.35", "reviewCount": "226"},
        "review": [{"reviewRating": {"ratingValue": "5"},
                   "reviewBody": "So good!",
                   "author": {"name": "Megan"},
                   "datePublished": "2025-11-13"}],
    })]
    reader.meta = {}
    recipe = crawl.from_jsonld(reader, "https://example.test/r")

    check("stated calories parsed out of 'kcal' unit string",
         recipe["nutrition_stated"]["calories"], 205.0)
    check("stated protein parsed out of 'g' unit string",
         recipe["nutrition_stated"]["protein_g"], 27.0)
    check("serving size carried through",
         recipe["nutrition_stated"]["serving_size"], "1 serving")
    check("rating still works (unrelated field, must not have broken)",
         recipe["source_rating"], {"value": 4.3, "count": 226})
    check("one review captured",
         len(recipe["source_reviews"]), 1)
    check("review author", recipe["source_reviews"][0]["author"], "Megan")
    check("review rating", recipe["source_reviews"][0]["rating"], 5.0)
    check("review body", recipe["source_reviews"][0]["body"], "So good!")

    # No nutrition/review block published at all -- neither field invents
    # anything, both come back empty rather than guessing.
    reader2 = crawl.Reader.__new__(crawl.Reader)
    reader2.ld = [_json.dumps({
        "@type": "Recipe", "name": "Plain",
        "recipeIngredient": ["1 cup flour"],
        "recipeInstructions": ["Bake."],
    })]
    reader2.meta = {}
    bare = crawl.from_jsonld(reader2, "https://example.test/plain")
    check("no nutrition block -> empty, not invented", bare["nutrition_stated"], {})
    check("no review field -> empty list, not invented", bare["source_reviews"], [])


def test_crawl_fill_ingredients_from_steps_backfills_bare_amounts():
    """Jason's own example: an ingredient line with no amount at all
    ("Kosher salt and freshly ground black pepper") gets one backfilled
    from what the STEPS actually say ("generously season..."), and an
    ingredient a step mentions but the list never had gets added -- but
    nothing is invented beyond what the steps themselves support."""
    import asyncio
    from app import llm

    async def fake_chat_json(system, user, **kw):
        check("the bare ingredient reaches the model",
             "Kosher salt and freshly ground black pepper" in user, True)
        check("the step text reaches the model",
             "Generously season" in user, True)
        return {
            "changed": [{"was": "Kosher salt and freshly ground black pepper",
                        "now": "Kosher salt and freshly ground black pepper, to taste"}],
            "added": ["A pinch of red pepper flakes"],
        }

    real = llm.chat_json
    llm.chat_json = fake_chat_json
    try:
        out = asyncio.run(crawl.fill_ingredients_from_steps(
            ["1 cup flour", "Kosher salt and freshly ground black pepper"],
            ["Generously season the chicken with the salt and pepper.",
             "Add a pinch of red pepper flakes at the end."],
        ))
    finally:
        llm.chat_json = real

    check("untouched line stays exactly as it was", out[0], "1 cup flour")
    check("bare line got the steps' own wording, not an invented amount",
         out[1], "Kosher salt and freshly ground black pepper, to taste")
    check("a step-only ingredient got added",
         "A pinch of red pepper flakes" in out, True)
    check("nothing got dropped", len(out), 3)


def test_crawl_fill_ingredients_from_steps_skips_the_model_when_nothing_is_bare():
    """THE FAST PATH STAYS FAST for the common case -- every line already
    has a real amount, so this must not spend a model call finding that
    out. If it did, this test would hang or fail on the unpatched
    llm.chat_json trying to reach a real router."""
    import asyncio
    out = asyncio.run(crawl.fill_ingredients_from_steps(
        ["1 cup flour", "2 tsp salt", "a pinch of pepper"],
        ["Mix everything together."],
    ))
    check("nothing touched, no model call attempted",
         out, ["1 cup flour", "2 tsp salt", "a pinch of pepper"])


def test_crawl_fill_ingredients_dedupes_a_relisted_bare_ingredient():
    """CAUGHT LIVE: the model sometimes answers with an "added" line that
    is really the SAME ingredient as an existing bare one, just phrased as
    a lead-in amount instead of an appended one ("Cooking spray or extra
    virgin olive oil" vs. a separately-added "A drizzle of olive oil").
    That has to replace the original line, not sit next to it as a
    near-duplicate."""
    import asyncio
    from app import llm

    async def fake_chat_json(system, user, **kw):
        return {"changed": [], "added": ["A drizzle of olive oil"]}

    real = llm.chat_json
    llm.chat_json = fake_chat_json
    try:
        out = asyncio.run(crawl.fill_ingredients_from_steps(
            ["Cooking spray or extra virgin olive oil", "5 garlic cloves"],
            ["Coat the pan with cooking spray or a drizzle of olive oil."],
        ))
    finally:
        llm.chat_json = real

    check("the vague line was replaced, not duplicated", len(out), 2)
    check("replaced with the more specific wording",
         out[0], "A drizzle of olive oil")
    check("an unrelated line is never touched by the dedupe",
         out[1], "5 garlic cloves")


def test_mealie_nutrition_falls_back_when_the_estimate_is_too_thin():
    """CAUGHT LIVE on a real recipe: one mis-parsed unit matching a single
    USDA baseline out of thirteen ingredients produced 453 kcal for a pot
    of chicken cacciatore that actually has 205. `used` being non-zero is
    not enough to trust the arithmetic -- a THIN match (few matched, not
    all of them) has to fall back to whatever the source page itself
    published, and any stale nutrition_full from an earlier bad estimate
    has to be cleared, not left standing."""
    import asyncio, tempfile, os, pathlib
    from app import core, mealie, nutrition, store

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    # 13 ingredients, only ONE has a baseline -- same shape as the live bug.
    matched_food = {"id": "food-tomato", "name": "crushed tomatoes", "extras": {}}
    ings = [{"food": matched_food, "unit": {"name": "cup"}, "quantity": 2}]
    ings += [{"food": {"id": f"food-{i}", "name": f"thing{i}", "extras": {}},
             "unit": None, "quantity": 0} for i in range(12)]
    recipe_full = {"recipeIngredient": ings, "recipeServings": 4,
                  "settings": {"showNutrition": False}}

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    old_db_path = core.DB_PATH
    core.DB_PATH = pathlib.Path(path)
    store.init()
    draft_id = store.save({
        "title": "Cacciatore",
        # A stale bad estimate from an earlier thin match -- this must get
        # cleared even though this call also has nothing trustworthy to
        # replace it with in Kitchen's own %DV chart.
        "nutrition_full": {"calories": 453.6},
        "nutrition_servings": 4,
        "nutrition_stated": {"calories": 205.0, "protein_g": 27.0},
    }, "cook@example.com")
    store.mark_kept(draft_id, "cacciatore-slug", "cook@example.com")

    patched = []

    async def fake_get(path, **kw):
        return FakeResponse(recipe_full)

    async def fake_ensure_baseline(food):
        if food["id"] == "food-tomato":
            return {"nutrients": {"calories": {"value": 1000.0, "unit": "KCAL"}}}
        return None

    async def fake_patch(path, payload, **kw):
        patched.append((path, payload))
        return FakeResponse({})

    real_get, real_ensure, real_patch = mealie._get, mealie._ensure_food_baseline, mealie._patch
    real_configured = nutrition.configured
    mealie._get, mealie._ensure_food_baseline, mealie._patch = fake_get, fake_ensure_baseline, fake_patch
    nutrition.configured = lambda: True
    try:
        ok = asyncio.run(mealie.push_recipe_nutrition("cacciatore-slug"))

        check("still reports success -- the stated fallback IS a real push", ok, True)
        check("exactly one Mealie PATCH happened (the fallback, not the thin estimate)",
             len(patched), 1)
        check("Mealie got the STATED number, not the thin one-ingredient estimate",
             patched[0][1]["nutrition"]["calories"], "205.0")

        draft = store.get(draft_id)
        check("the stale bad nutrition_full got cleared, not left standing",
             draft["recipe"].get("nutrition_full"), None)
    finally:
        mealie._get, mealie._ensure_food_baseline, mealie._patch = real_get, real_ensure, real_patch
        nutrition.configured = real_configured
        core.DB_PATH = old_db_path


def test_guide_needs_no_model():
    """The whole point: a built guide involves no network call at all."""
    recipe = crawl.from_jsonld(crawl.read(PAGE), "https://ex.test/soup")
    recipe["images"] = []
    view = importer.build_guide(recipe, want_servings=6)
    check("factor", view["factor"], Fraction(3, 2))
    check("scaled leeks", view["ingredients"][0]["text"], "3 leeks, sliced")
    check("serves now", view["serves_now"], 4)
    check("times warned", view["times_note"], True)
    check("steps drawn", all(s["svg"] or s["photo"] for s in view["steps"]), True)


# ---------------------------------------------------------------------------
def test_mealie_tags_and_category_travel_with_every_import():
    """207 recipes went in with an empty tag rail because from_url() only
    ever asked Mealie to scrape the page and stopped there -- the labels,
    course and cuisine this app already worked out never made the trip.
    Pins the shape of what now gets sent, both for the from_recipe() path
    (always carried them) and the from_url() follow-up patch (didn't)."""
    from app import mealie

    recipe = {"title": "Fried Chicken", "labels": ["chicken", "quick"],
             "course": "Dinner", "cuisine": "Southern",
             "ingredients": ["2 lb chicken"], "steps": ["Fry it."]}

    tags = mealie._tags_for(recipe)
    names = {t["name"] for t in tags}
    check("labels present", {"chicken", "quick"} <= names, True)
    check("course present as a tag too", "Dinner" in names, True)
    check("cuisine present as a tag too", "Southern" in names, True)
    check("no duplicate tags", len(tags), len(names))
    # CAUGHT LIVE: {"name": ...} alone 422s -- Mealie requires `slug` on
    # every tag/category object, not just `name`.
    check("every tag carries a slug", all(t.get("slug") for t in tags), True)

    category = mealie._category_for(recipe)
    check("category is course, not cuisine or labels",
         category, [{"name": "Dinner", "slug": "dinner"}])

    check("recipeCategory present in the full patch body",
         "recipeCategory" in mealie._to_mealie(recipe), True)

    check("no category when there is no course",
         mealie._category_for({"course": ""}), [])

    check("slugify handles punctuation and spaces",
         mealie._slugify("Side Dish / Snack!"), "side-dish-snack")


def test_mealie_tools_travel_with_every_import():
    """Equipment, same shape as tags/category above -- Mealie's own Recipe
    Finder filters by tools too, and a recipe with none imported is
    indistinguishable from one that genuinely needs none."""
    from app import mealie

    recipe = {"title": "Dutch Oven Bread",
             "tools": ["Dutch oven", "Dutch oven", "Kitchen scale"]}
    tools = mealie._tools_for(recipe)
    names = [t["name"] for t in tools]
    check("tools carried through", names, ["Dutch oven", "Kitchen scale"])
    check("duplicates collapsed", len(tools), 2)
    check("every tool carries a slug", all(t.get("slug") for t in tools), True)
    check("no tools when the recipe has none", mealie._tools_for({}), [])
    check("tools present in the full patch body",
         "tools" in mealie._to_mealie(recipe), True)


def test_resolve_tag_like_reuses_the_existing_name():
    """CAUGHT LIVE, THE HARD WAY: {"name": "soup", "slug": "soup"} with no
    `id`, even though a tag named exactly "soup" with slug "soup" already
    exists, makes Mealie try to CREATE a new one anyway -- 400, "Recipe
    already exists". Only sending the EXISTING record's real id actually
    attaches it instead of recreating it."""
    import asyncio
    from app import mealie

    class FakeResponse:
        def __init__(self, items):
            self._items = items

        def json(self):
            return {"items": self._items}

    async def fake_get(path, **kw):
        if "organizers/tags" in path:
            return FakeResponse([{"id": "real-id-123", "name": "soup", "slug": "soup"}])
        return FakeResponse([])

    real_get = mealie._get
    mealie._get = fake_get
    try:
        out = asyncio.run(mealie._resolve_tag_like("tags", [
            {"name": "soup", "slug": "soup"},
            {"name": "brand-new-thing", "slug": "brand-new-thing"},
        ]))
    finally:
        mealie._get = real_get

    check("an existing slug gets its real id attached",
         out[0], {"id": "real-id-123", "name": "soup", "slug": "soup"})
    check("a genuinely new tag is left alone, no id invented",
         out[1], {"name": "brand-new-thing", "slug": "brand-new-thing"})

    check("no candidates -> no network call at all",
         asyncio.run(mealie._resolve_tag_like("tags", [])), [])


def test_resolve_tag_like_creates_new_tools_first():
    """CAUGHT LIVE, A SECOND TIME: tools are not like tags/categories --
    Mealie's RecipeTool-Input requires `id` even for something brand new.
    A genuinely new tool has to be created with its own POST first, here,
    to get a real id before it is ever attached to a recipe."""
    import asyncio
    from app import mealie

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    posted = []

    async def fake_get(path, **kw):
        return FakeResponse({"items": []})  # nothing exists yet

    async def fake_post(path, payload, **kw):
        posted.append((path, payload))
        return FakeResponse({"id": "new-tool-id", "name": payload["name"],
                            "slug": payload["name"].lower().replace(" ", "-")})

    real_get, real_post = mealie._get, mealie._post
    mealie._get, mealie._post = fake_get, fake_post
    try:
        out = asyncio.run(mealie._resolve_tag_like("tools", [
            {"name": "Stand Mixer", "slug": "stand-mixer"},
        ]))
    finally:
        mealie._get, mealie._post = real_get, real_post

    check("a new tool is created via POST, not left id-less",
         out[0], {"id": "new-tool-id", "name": "Stand Mixer", "slug": "stand-mixer"})
    check("exactly one POST made", len(posted), 1)
    check("POST hit the tools organizer endpoint",
         "organizers/tools" in posted[0][0], True)


def test_all_foods_paginates_using_the_real_field_name():
    """CAUGHT LIVE: Mealie's pagination field is `total_pages`, snake_case
    -- not `totalPages`, the camelCase shape every other field in this API
    uses. Checking the wrong key silently stops after page one instead of
    erroring, which is the kind of bug that looks like "it worked" until
    someone counts the results (785 foods read back as 100)."""
    import asyncio
    from app import mealie

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    calls = []

    async def fake_get(path, **kw):
        calls.append(path)
        if "page=1" in path:
            return FakeResponse({"items": [{"name": "flour"}, {"name": "salt"}],
                                 "total_pages": 2})
        return FakeResponse({"items": [{"name": "sugar"}], "total_pages": 2})

    real_get = mealie._get
    mealie._get = fake_get
    try:
        out = asyncio.run(mealie.all_foods())
    finally:
        mealie._get = real_get

    check("both pages were actually fetched", len(calls), 2)
    check("every item across both pages came back",
         [f["name"] for f in out], ["flour", "salt", "sugar"])


def test_prune_organizers_deletes_only_what_no_recipe_uses():
    """Mostly cleans up after the tools-guessing bug: fixing the prompt
    stopped NEW garbage tools from being created, but the old orphaned
    ones -- no longer attached to any recipe once the backfill overwrote
    them -- were still sitting in Mealie's tools list. This is the
    general-purpose version, covering tags and categories too."""
    import asyncio
    import mealie_fix as mf

    m = mf.Mealie(write=True)

    recipes_page = {"items": [
        {"tags": [{"id": "tag-used"}], "recipeCategory": [{"id": "cat-used"}],
         "tools": [{"id": "tool-used"}]},
    ]}
    tags_page = {"items": [{"id": "tag-used", "name": "used-tag"},
                           {"id": "tag-orphan", "name": "orphan-tag"}]}
    categories_page = {"items": [{"id": "cat-used", "name": "used-cat"}]}
    tools_page = {"items": [{"id": "tool-used", "name": "used-tool"},
                            {"id": "tool-orphan", "name": "orphan-tool"}]}

    deleted = []

    async def fake_get(path, **kw):
        if path.startswith("/api/recipes"):
            return recipes_page
        if path.startswith("/api/organizers/tags"):
            return tags_page
        if path.startswith("/api/organizers/categories"):
            return categories_page
        if path.startswith("/api/organizers/tools"):
            return tools_page
        raise AssertionError(f"unexpected GET {path}")

    async def fake_delete(path):
        deleted.append(path)
        return {}

    m.get = fake_get
    m.delete = fake_delete
    rc = asyncio.run(mf.cmd_prune_organizers(m))
    check("cmd_prune_organizers returns 0", rc, 0)
    check("exactly the two orphans were deleted, nothing used",
         sorted(deleted),
         sorted(["/api/organizers/tags/tag-orphan", "/api/organizers/tools/tool-orphan"]))


def test_tools_extracted_from_jsonld():
    """schema.org `tool` -- a plain string, a HowToTool object, or a list
    of either. Most sites don't publish this; that has to come back as an
    empty list, not an error."""
    from app import crawl

    check("plain strings", crawl._tools_from_ld(["Whisk", "Oven"]), ["Whisk", "Oven"])
    check("HowToTool objects",
         crawl._tools_from_ld([{"@type": "HowToTool", "name": "Stand mixer"}]),
         ["Stand mixer"])
    check("a bare string, not a list", crawl._tools_from_ld("Rolling pin"), ["Rolling pin"])
    check("nothing published -> empty list, not an error",
         crawl._tools_from_ld(None), [])
    check("blank names dropped",
         crawl._tools_from_ld([{"name": ""}, {"name": "Sieve"}]), ["Sieve"])
    # CAUGHT LIVE: some sites' recipe plugins stuff a shopping widget's link
    # text into schema.org's `tool` field alongside the real equipment.
    check("shopping/affiliate junk dropped, real tools kept",
         crawl._tools_from_ld(["Amazon", "Whisk", "Walmart", "Dutch oven"]),
         ["Whisk", "Dutch oven"])
    check("case and punctuation don't dodge the filter",
         crawl._tools_from_ld(["AMAZON", "Shop Now!", "Sponsored"]), [])


# ---------------------------------------------------------------------------
def test_step_illustration_captions():
    """caption_for() uses a model (unlike prompt_for(), deliberately -- see
    the module docstring) and must never raise even when the model can't be
    reached, so a picture that drew fine is never lost over a caption."""
    import asyncio
    from app import art, llm

    async def fake_chat(system, user, **kw):
        return '"A drawing of onions caramelizing in a pan."'

    real = llm.chat
    llm.chat = fake_chat
    try:
        out = asyncio.run(art.caption_for("cook the onions until golden", "Soup"))
    finally:
        llm.chat = real
    check("quotes stripped from the model's reply",
         out, "A drawing of onions caramelizing in a pan.")

    async def fails(system, user, **kw):
        raise llm.LLMError("no route to the model")

    llm.chat = fails
    try:
        out = asyncio.run(art.caption_for("cook the onions", "Soup"))
    finally:
        llm.chat = real
    check("falls back to a plain caption rather than raising",
         "drawing" in out.lower() and "photo" in out.lower(), True)


def test_push_step_illustration_never_raises_when_unconfigured():
    """Same contract as every other write in mealie.py: no token set means
    nothing happens, quietly, rather than an exception surfacing out of the
    idle artist's background loop."""
    import asyncio
    from app import mealie

    ok = asyncio.run(mealie.push_step_illustration(
        "some-recipe", 1, b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "a caption"))
    check("returns False rather than raising", ok, False)


def test_split_leading_paren_from_food_rescues_a_unit_aside():
    """CAUGHT LIVE: Mealie's own parser correctly pulls the quantity and
    unit off "1 1/4 c. (2 1/2 sticks) unsalted butter, melted" (quantity
    1.25, unit "c.") but leaves the food as "(2 1/2 sticks) unsalted
    butter" instead of "unsalted butter" -- the parenthetical is a real,
    meaningful aside (a stick count), not garbage, so it moves to the
    note instead of being thrown away."""
    from app import mealie

    food, note = mealie._split_leading_paren_from_food(
        "(2 1/2 sticks) unsalted butter", "melted")
    check("food name with the aside stripped", food, "unsalted butter")
    check("aside moved to the front of the note", note, "(2 1/2 sticks), melted")

    food, note = mealie._split_leading_paren_from_food("(320 g.) all-purpose flour", "")
    check("food name with no existing note", food, "all-purpose flour")
    check("aside becomes the whole note", note, "(320 g.)")

    # A food name with NO leading parenthetical is returned unchanged.
    food, note = mealie._split_leading_paren_from_food("kosher salt", "")
    check("unaffected food name", food, "kosher salt")
    check("unaffected note", note, "")

    # The whole "food" WAS the parenthetical -- nothing real left over,
    # so this is left alone rather than turned into an empty food name.
    food, note = mealie._split_leading_paren_from_food("(garnish only)", "")
    check("nothing to rescue -- left as-is", food, "(garnish only)")


def test_food_name_bad_catches_leading_quantity_and_unbalanced_parens():
    from app import mealie

    for bad in ("(60 g.) all-purpose flour", "+ 2 Tbsp granulated sugar",
               "6 tbsp unsalted butter", "28-ounce can whole peeled tomatoes",
               "red pepper flakes (chilli flakes", "sliced olives ("):
        check(f"{bad!r} is flagged bad", mealie._food_name_bad(bad), True)
    for ok in ("unsalted butter", "all-purpose flour", "kosher salt",
              "oregano (dried)", "powdered sugar (confectioners' sugar)"):
        check(f"{ok!r} is NOT flagged", mealie._food_name_bad(ok), False)


def test_find_or_create_never_raises_on_a_db_collision():
    """CAUGHT LIVE: a garbage unit name like "." (brute's parse of "a
    drizzle of olive oil" has nothing better to call the unit) hit a real
    409 from Mealie -- 'UNIQUE constraint failed' on a name that
    slugifies to nothing. Before this was fixed, that exception escaped
    _find_or_create, was caught by parse_ingredients()'s own outer
    try/except, and threw away every other line's already-correct parse
    for the WHOLE recipe -- one bad unit name meant a recipe's ingredient
    notes silently never got fixed, no matter how many times this ran.
    """
    import asyncio
    from app import mealie

    class _FakeResp:
        def __init__(self, status=200, payload=None):
            self.status_code = status
            self._payload = payload or {}

        def json(self):
            return self._payload

    real_get, real_post = mealie._get, mealie._post
    try:
        async def fake_get(*a, **kw):
            return _FakeResp(200, {"items": []})

        async def fake_post(*a, **kw):
            raise mealie.MealieError("Mealie said 409: UNIQUE constraint failed")

        mealie._get = fake_get
        mealie._post = fake_post
        got = asyncio.run(mealie._find_or_create("units", "."))
        check("returns empty string rather than raising", got, "")
    finally:
        mealie._get, mealie._post = real_get, real_post


def test_garbled_catches_real_parser_junk_but_not_a_real_alternative():
    """A leading comma or a doubled comma is never legitimate English, so
    those are always garbled. A leading "or" is different: it is exactly
    right when the SOURCE line really did offer an alternative ("chicken
    or turkey"), and only garbled when the parser invented a word that
    isn't in the original at all -- CAUGHT LIVE, an earlier version of
    this check flagged every leading "or" and "fixed" a few hundred
    perfectly good notes for no reason."""
    from app import mealie

    check("a leading comma is always garbled",
          mealie._garbled(", finely chopped, or pressed", "5 garlic cloves, finely chopped or pressed"), True)
    check("a doubled comma is always garbled",
          mealie._garbled("large, , chopped", "1/2 large yellow onion, chopped"), True)
    check("a leading 'or' the source never said is garbled",
          mealie._garbled("or 5 ounce trimmed",
                          "10 bone-in skinless chicken thighs (, about 5 ounces each, trimmed)"), True)
    check("a leading 'or' the source really did say is NOT garbled",
          mealie._garbled("or ground turkey", "1 lb. ground chicken or turkey"), False)
    check("a leading 'or' with no original given stays cautious (garbled)",
          mealie._garbled("or 1 cup finely grated"), True)
    check("an ordinary note is not garbled",
          mealie._garbled("for garnish", "Fresh parsley for garnish"), False)


def test_drawn_and_scraped_step_regexes_strip_only_their_own_kind():
    """Same reasoning as the own-photo regex, for the other two embed
    kinds. CAUGHT LIVE: neither of these regexes existed before -- every
    call to push_step_illustration()/push_step_photo() just appended,
    with nothing stripping a previous embed first. A real recipe (Peach
    Cobbler) ended up with 41 duplicate "AI-drawn illustration" embeds
    stacked on some steps because of it (see main.py's _draw_each for the
    actual sequence of events: a container restart mid-draw lost the
    in-memory "already drawn" record, so the idle artist redrew and
    re-pushed the same step every time it came back around)."""
    from app import mealie

    drawn = ("Do the thing.\n\n![AI-drawn illustration]"
            "(/api/media/recipes/x/assets/step-1.png)\n"
            "*A drawing of a bowl of peaches.*")
    check("strips a real drawn-illustration embed cleanly",
          mealie._DRAWN_STEP_RE.sub("", drawn), "Do the thing.")
    check("drawn regex leaves an own-photo embed alone",
          mealie._DRAWN_STEP_RE.sub("", "X\n\n![Your own photo of this step](u)\n"
                                    "*Your own photo, added in Kitchen.*"),
          "X\n\n![Your own photo of this step](u)\n*Your own photo, added in Kitchen.*")

    scraped = ("Do the thing.\n\n![a caption from the page]"
              "(/api/media/recipes/x/assets/step-1.jpg)\n"
              "*From the recipe's own page.*")
    check("strips a real scraped-photo embed cleanly",
          mealie._SCRAPED_STEP_PHOTO_RE.sub("", scraped), "Do the thing.")
    check("scraped regex leaves a drawn illustration's embed alone",
          mealie._SCRAPED_STEP_PHOTO_RE.sub("", drawn), drawn)

    # Two drawn embeds stacked (the actual shape of the live bug) -- the
    # regex has to remove EACH one it finds, not just the first, so a
    # step that already has several duplicates gets fully cleaned by one
    # sub() call, not just trimmed by one.
    doubled = drawn + drawn
    check("removes every stacked duplicate in one pass, not just one",
          mealie._DRAWN_STEP_RE.sub("", doubled), "Do the thing.Do the thing.")


def test_own_step_photo_regex_strips_only_its_own_embed():
    """The regex push_own_step_photo/remove_own_step_photo use to find and
    replace an earlier own-photo embed must never touch a scraped photo's
    or an AI-drawn illustration's embed -- those use different wording on
    purpose, precisely so this can tell them apart."""
    from app import mealie

    own = ("Do the thing.\n\n![Your own photo of this step]"
          "(/api/media/recipes/x/assets/step-1.jpg)\n"
          "*Your own photo, added in Kitchen.*")
    check("strips a real own-photo embed cleanly",
          mealie._OWN_STEP_PHOTO_RE.sub("", own), "Do the thing.")

    scraped = ("Do the thing.\n\n![alt text](/api/media/recipes/x/assets/y.jpg)\n"
              "*From the recipe's own page.*")
    check("leaves a scraped photo's embed alone",
          mealie._OWN_STEP_PHOTO_RE.sub("", scraped), scraped)

    drawn = ("Do the thing.\n\n![AI-drawn illustration](/api/media/recipes/x/assets/y.png)\n"
            "*a caption*")
    check("leaves a drawn illustration's embed alone",
          mealie._OWN_STEP_PHOTO_RE.sub("", drawn), drawn)


def test_push_own_step_photo_never_raises_for_a_recipe_mealie_does_not_have():
    """Same contract as push_step_illustration -- a slug Mealie has never
    heard of (or any other failure reaching it) returns False, it never
    raises out of the upload route."""
    import asyncio
    from app import mealie

    ok = asyncio.run(mealie.push_own_step_photo(
        "some-recipe-that-does-not-exist", 1, b"\xff\xd8\xff" + b"\x00" * 20, "jpg"))
    check("returns False rather than raising", ok, False)

    ok = asyncio.run(mealie.push_own_step_photo(
        "some-recipe-that-does-not-exist", 1, b"\x00" * 20, "bmp"))
    check("an extension with no known mime also returns False", ok, False)


def test_remove_own_step_photo_never_raises_for_a_recipe_mealie_does_not_have():
    import asyncio
    from app import mealie

    ok = asyncio.run(mealie.remove_own_step_photo(
        "some-recipe-that-does-not-exist", 1))
    check("returns False rather than raising", ok, False)


def test_push_step_photo_downloads_before_it_ever_reaches_mealie():
    """A real page photo has no bytes yet -- push_step_photo fetches the URL
    first. A failed fetch or a non-image response must stop there, never
    falling through to _push_step_asset with garbage."""
    import asyncio
    from app import mealie

    class _FakeResp:
        def __init__(self, status=200, content=b"", ctype="image/jpeg"):
            self.status_code = status
            self.content = content
            self.headers = {"content-type": ctype}

    class _FakeClient:
        def __init__(self, resp):
            self._resp = resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return self._resp

    real = mealie.httpx.AsyncClient
    try:
        mealie.httpx.AsyncClient = lambda **kw: _FakeClient(_FakeResp(status=404))
        ok = asyncio.run(mealie.push_step_photo("some-recipe", 1, "https://ex.test/photo.jpg"))
        check("a failed download returns False, not a raise", ok, False)

        mealie.httpx.AsyncClient = lambda **kw: _FakeClient(_FakeResp(ctype="text/html"))
        ok = asyncio.run(mealie.push_step_photo("some-recipe", 1, "https://ex.test/photo.jpg"))
        check("a non-image content-type is refused, not guessed at", ok, False)

        big = b"x" * (mealie.MAX_STEP_PHOTO_BYTES + 1)
        mealie.httpx.AsyncClient = lambda **kw: _FakeClient(_FakeResp(content=big))
        ok = asyncio.run(mealie.push_step_photo("some-recipe", 1, "https://ex.test/photo.jpg"))
        check("an oversized image is refused too", ok, False)
    finally:
        mealie.httpx.AsyncClient = real


# ---------------------------------------------------------------------------
def test_nutrition_extract_picks_kcal_not_kj():
    """FDC lists Energy twice -- once in KCAL, once in kJ, as separate rows.
    Getting the wrong one silently makes every calorie count 4x too high."""
    from app import nutrition

    fake_nutrients = [
        {"nutrientName": "Energy", "unitName": "kJ", "value": 1530.0},
        {"nutrientName": "Energy", "unitName": "KCAL", "value": 364.0},
        {"nutrientName": "Protein", "unitName": "G", "value": 10.3},
        {"nutrientName": "Total lipid (fat)", "unitName": "G", "value": 1.0},
        {"nutrientName": "Iron, Fe", "unitName": "MG", "value": 4.6},
    ]
    out = nutrition._extract(fake_nutrients)
    check("picks the KCAL row, not the kJ row",
         out["calories"]["value"], 364.0)
    check("protein carried through", out["protein_g"]["value"], 10.3)
    check("a micronutrient carried through too", out["iron_mg"]["value"], 4.6)
    check("a nutrient with no match is simply absent",
         "vitamin_c_mg" in out, False)


def test_recipe_nutrition_grams_for():
    from app import recipe_nutrition as rn

    check("2 cups", round(rn.grams_for(2, "cups"), 1), 473.2)
    check("1 tablespoon", round(rn.grams_for(1, "tablespoon"), 2), 14.79)
    check("unit-less/countable ('egg') has no conversion",
         rn.grams_for(3, "egg"), None)
    check("zero quantity converts to nothing, not zero grams",
         rn.grams_for(0, "cup"), None)


def test_recipe_nutrition_estimate_is_arithmetic_not_a_lookup():
    """The whole point: one baseline (flour, per 100g), used for a recipe
    that calls for 200g of it, should come out to exactly double -- no
    second network call, no model, just multiplication."""
    from app import recipe_nutrition as rn

    baselines = {
        "flour": {"nutrients": {
            "calories": {"value": 364.0, "unit": "KCAL"},
            "protein_g": {"value": 10.0, "unit": "G"},
        }},
    }
    ingredients = [
        {"food": {"name": "Flour"}, "unit": {"name": "gram"}, "quantity": 200},
        {"food": {"name": "an unknown ingredient"}, "unit": {"name": "gram"}, "quantity": 50},
        {"food": {"name": "Flour"}, "unit": {"name": "egg"}, "quantity": 1},
    ]
    est, used, total = rn.estimate(ingredients, baselines)
    check("only the one convertible, known-baseline ingredient counted",
         used, 1)
    check("three ingredients were on the list", total, 3)
    check("200g of flour is double the 100g baseline (calories)",
         est["calories"], "728.0")
    check("200g of flour is double the 100g baseline (protein)",
         est["proteinContent"], "20.0")
    check("no micronutrient field leaks into Mealie's macro-only shape",
         "iron_mg" not in est and "ironContent" not in est, True)

    empty, used0, total0 = rn.estimate(
        [{"food": {"name": "nope"}, "unit": {"name": "gram"}, "quantity": 5}],
        baselines)
    check("no matching baseline at all -> nothing estimated", empty, {})


def test_totals_for_carries_vitamins_estimate_does_not():
    """estimate() narrows to what Mealie's own nutrition field can hold;
    totals_for() is the full thing -- this is the one guarantee the whole
    %DV chart is built on."""
    from app import recipe_nutrition as rn

    baselines = {
        "spinach": {"nutrients": {
            "calories": {"value": 23.0, "unit": "KCAL"},
            "vitamin_a_ug": {"value": 469.0, "unit": "UG"},
            "vitamin_c_mg": {"value": 28.1, "unit": "MG"},
            "iron_mg": {"value": 2.7, "unit": "MG"},
        }},
    }
    ingredients = [{"food": {"name": "Spinach"}, "unit": {"name": "gram"}, "quantity": 200}]
    totals, used, total = rn.totals_for(ingredients, baselines)
    check("totals_for carries the vitamin", round(totals["vitamin_a_ug"], 1), 938.0)
    check("totals_for carries the mineral too", round(totals["iron_mg"], 2), 5.4)

    est = rn.mealie_shape(totals)
    check("mealie_shape drops what Mealie's field cannot hold",
         "vitamin_a_ug" not in est and "iron_mg" not in est, True)
    check("mealie_shape keeps what it can", est["calories"], "46.0")


def test_daily_values_scales_per_serving_and_sorts_highest_first():
    from app import recipe_nutrition as rn

    # A whole recipe's worth, for FOUR servings.
    totals = {"calories": 2000.0, "sodium_mg": 4600.0, "iron_mg": 3.6,
             "sugar_g": 40.0}
    rows = rn.daily_values(totals, servings=4)
    by_key = {r["key"]: r for r in rows}

    check("per-serving calories is a quarter of the recipe total",
         by_key["calories"]["amount"], 500.0)
    check("500 of a 2000 daily value is 25%", by_key["calories"]["pct"], 25)
    check("1150mg sodium of 2300 daily value is 50%", by_key["sodium_mg"]["pct"], 50)
    check("sorted highest %DV first", rows[0]["key"], "sodium_mg")
    check("sugar has no FDA daily value, so it never appears here",
         "sugar_g" not in by_key, True)
    check("a nutrient never measured for this recipe is simply absent, "
         "not shown as 0%", "vitamin_c_mg" not in by_key, True)

    extra = rn.extra_amounts(totals, servings=4)
    check("sugar shows up as a plain amount instead", extra[0]["key"], "sugar_g")
    check("also scaled per serving", extra[0]["amount"], 10.0)

    check("a zero or missing servings count falls back to 1, not a crash",
         rn.daily_values(totals, servings=0)[0]["amount"] > 0, True)


def test_batch_totals_scales_with_the_portion_you_pick():
    """The one nutrition number that is SUPPOSED to move when the portion
    buttons are pressed -- daily_values() deliberately does not (see the
    test above: per serving is portion-count-invariant on purpose)."""
    from app import recipe_nutrition as rn

    # A recipe written for 4, 2000 kcal total -- 500 kcal/serving.
    totals = {"calories": 2000.0, "protein_g": 80.0, "fat_g": 60.0, "carbs_g": 220.0}

    same = rn.batch_totals(totals, native_servings=4, servings_now=4)
    check("making the recipe as written returns the recipe's own total",
         {r["key"]: r["amount"] for r in same},
         {"calories": 2000.0, "protein_g": 80.0, "fat_g": 60.0, "carbs_g": 220.0})

    doubled = rn.batch_totals(totals, native_servings=4, servings_now=8)
    check("making twice as many doubles the batch total",
         next(r["amount"] for r in doubled if r["key"] == "calories"), 4000.0)

    quartered = rn.batch_totals(totals, native_servings=4, servings_now=1)
    check("making one portion out of a 4-serving recipe is a quarter of the total",
         next(r["amount"] for r in quartered if r["key"] == "calories"), 500.0)
    per_serving = {r["key"]: r["amount"] for r in rn.daily_values(totals, servings=4)}
    check("that quarter matches what one serving actually has in it",
         next(r["amount"] for r in quartered if r["key"] == "calories"),
         per_serving["calories"])

    check("no whole-recipe nutrition at all -> nothing to show, not a crash",
         rn.batch_totals({}, 4, 4), [])
    check("no native serving count -> nothing to show, not a crash",
         rn.batch_totals(totals, 0, 4), [])
    check("a missing/zero servings_now falls back to the native count",
         rn.batch_totals(totals, native_servings=4, servings_now=0),
         rn.batch_totals(totals, native_servings=4, servings_now=4))


def test_push_recipe_nutrition_runs_automatically_on_every_keep():
    """The gap this closes: nutrition used to reach Mealie only from a
    manual `nutrition_fix.py --backfill` run, so a recipe kept today had no
    numbers until someone remembered to run it. keep() now backgrounds
    push_recipe_nutrition() itself -- this pins the whole live path: an
    ingredient with no baseline yet gets one looked up and saved, a second
    recipe reusing that same food does not look it up again, and the
    recipe's own nutrition field is the arithmetic result, not a guess."""
    import asyncio, tempfile, os, pathlib
    from app import core, mealie, nutrition, store

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    flour_food = {"id": "food-flour", "name": "flour", "extras": {}}
    salt_food = {"id": "food-salt", "name": "salt",
                "extras": {"nutrition": '{"nutrients": {"calories": {"value": 0.0, "unit": "KCAL"}}}'}}
    recipe_full = {"recipeIngredient": [
        {"food": flour_food, "unit": {"name": "gram"}, "quantity": 200},
        {"food": salt_food, "unit": {"name": "gram"}, "quantity": 5},
    ], "recipeServings": 2,
       "settings": {"public": True, "showNutrition": False, "landscapeView": True}}

    # A real Kitchen draft already kept under this slug -- push_recipe_
    # nutrition() writes the full totals back onto it, and that write must
    # never take down the Mealie push above it if it goes wrong.
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    old_db_path = core.DB_PATH
    core.DB_PATH = pathlib.Path(path)
    store.init()
    draft_id = store.save({"title": "Bread"}, "baker@example.com")
    store.mark_kept(draft_id, "some-slug", "baker@example.com")

    looked_up, put_calls, patched = [], [], []

    async def fake_get(path, **kw):
        assert path == "/api/recipes/some-slug"
        return FakeResponse(recipe_full)

    async def fake_search(name):
        looked_up.append(name)
        return {"matchedName": name, "dataType": "Foundation",
               "nutrients": {"calories": {"value": 364.0, "unit": "KCAL"}}}

    async def fake_put(path, payload, **kw):
        put_calls.append((path, payload))
        # Mutate the same dict fake_get keeps handing back, the way a real
        # PUT followed by a real GET would show the new extras.
        if payload.get("id") == flour_food["id"]:
            flour_food["extras"] = payload["extras"]
        return FakeResponse({})

    async def fake_patch(path, payload, **kw):
        patched.append((path, payload))
        return FakeResponse({})

    real_get, real_put, real_patch = mealie._get, mealie._put, mealie._patch
    real_search, real_configured = nutrition.search, nutrition.configured
    mealie._get, mealie._put, mealie._patch = fake_get, fake_put, fake_patch
    nutrition.search = fake_search
    nutrition.configured = lambda: True
    try:
        ok = asyncio.run(mealie.push_recipe_nutrition("some-slug"))

        check("reports success", ok, True)
        check("only the food with no baseline yet was looked up",
             looked_up, ["flour"])
        check("that lookup was saved back to the food so it is never looked up again",
             len(put_calls), 1)
        check("it saved onto the right food",
             put_calls[0][1]["id"], flour_food["id"])
        check("the new baseline is JSON-encoded, not a nested dict",
             isinstance(put_calls[0][1]["extras"]["nutrition"], str), True)
        check("and it actually holds what USDA returned",
             "364.0" in put_calls[0][1]["extras"]["nutrition"], True)
        check("the recipe itself got patched with the arithmetic result",
             len(patched), 1)
        check("200g of flour at 364 kcal/100g is 728",
             patched[0][1]["nutrition"]["calories"], "728.0")
        check("CAUGHT LIVE: showNutrition is flipped on, or Mealie's own "
             "frontend hides a perfectly good nutrition panel",
             patched[0][1]["settings"]["showNutrition"], True)
        check("the rest of the recipe's settings survive the patch untouched",
             patched[0][1]["settings"]["landscapeView"], True)

        saved = store.get_by_slug("some-slug")["recipe"]
        check("the full totals -- not just Mealie's macro subset -- landed "
             "in Kitchen's own record", "calories" in saved.get("nutrition_full", {}), True)
        check("the recipe's own serving count was recorded alongside it",
             saved.get("nutrition_servings"), 2)

        # A food that already has a baseline (flour, just saved above, and
        # salt, which started with one) is never looked up or re-saved.
        looked_up.clear(); put_calls.clear()
        asyncio.run(mealie.push_recipe_nutrition("some-slug"))
        check("re-running costs no new USDA lookups -- both foods now cached",
             looked_up, [])
        check("and nothing gets re-PUT either", put_calls, [])

        # USDA not configured at all -> no crash, just no nutrition.
        nutrition.configured = lambda: False
        ok = asyncio.run(mealie.push_recipe_nutrition("some-slug"))
        check("unconfigured USDA reports False, not an exception", ok, False)
    finally:
        mealie._get, mealie._put, mealie._patch = real_get, real_put, real_patch
        nutrition.search = real_search
        nutrition.configured = real_configured
        core.DB_PATH = old_db_path
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
def test_nutrition_extras_are_json_strings_not_dicts():
    """CAUGHT LIVE: Mealie stores each `extras` value as its own SQL row
    (ingredient_food_extras), and the column is a plain string -- PUTting a
    nested dict there 400s with a raw sqlite3.ProgrammingError, not a
    friendly validation message. _load_nutrition() has to decode what
    nutrition_fix.py encoded, or every food backfilled with the bug still
    in place would read back as having no baseline."""
    import json
    import nutrition_fix as nf

    check("no extras at all -> None", nf._load_nutrition({}), None)
    check("garbage string -> None, not a crash",
         nf._load_nutrition({"nutrition": "{not json"}), None)

    original = {"source": "USDA FoodData Central",
               "nutrients": {"calories": {"value": 100.0, "unit": "KCAL"}}}
    stored = {"nutrition": json.dumps(original)}
    check("round-trips through the JSON-string encoding",
         nf._load_nutrition(stored), original)


def test_used_in_defaults_to_empty_not_none():
    """"used in nothing" is a real, common answer -- not a missing-data
    case the way an unlooked-up nutrition baseline is. Empty list, not
    None, so callers can iterate it without a null check."""
    import json
    import nutrition_fix as nf

    check("no extras at all -> []", nf._load_used_in({}), [])
    names = ["Chicken Parmesan", "Chicken Noodle Soup"]
    check("round-trips through the JSON-string encoding",
         nf._load_used_in({"usedIn": json.dumps(names)}), names)


# ---------------------------------------------------------------------------
def test_source_rating_from_jsonld():
    """Feedback from the ORIGINAL site's own aggregateRating -- a star value
    and a count, nothing else. Review text is deliberately never pulled in;
    see crawl.py's _rating_from_ld() docstring for why."""
    check("normal case",
         crawl._rating_from_ld({"ratingValue": "4.7", "ratingCount": "823"}),
         {"value": 4.7, "count": 823})
    check("reviewCount used when ratingCount is missing",
         crawl._rating_from_ld({"ratingValue": 4.2, "reviewCount": 10}),
         {"value": 4.2, "count": 10})
    check("not a dict at all -> nothing", crawl._rating_from_ld("4.5 stars"), {})
    check("out of range value rejected, not clamped",
         crawl._rating_from_ld({"ratingValue": 9.9, "ratingCount": 5}), {})
    check("garbage value -> nothing, not a crash",
         crawl._rating_from_ld({"ratingValue": "not a number"}), {})
    check("no count at all still returns the rating",
         crawl._rating_from_ld({"ratingValue": 4.0}), {"value": 4.0, "count": 0})


# ---------------------------------------------------------------------------
def test_nutrition_llm_fallback_only_when_usda_has_nothing():
    """189 foods had no USDA match at all. llm_guess() exists for exactly
    those -- pinned here as clearly marked estimates, never silently
    presented the same way as real USDA data."""
    import asyncio
    from app import llm, nutrition

    async def fake_chat_json(system, user, **kw):
        return {"is_food": True, "calories": 52, "protein_g": 0.3,
               "carbs_g": 14, "sodium_mg": 1}

    real = llm.chat_json
    llm.chat_json = fake_chat_json
    try:
        out = asyncio.run(nutrition.llm_guess("a very obscure heirloom apple"))
    finally:
        llm.chat_json = real
    check("estimate is clearly labelled, not passed off as measured",
         out["dataType"], "LLM estimate")
    check("the source string says plainly it is not measured",
         "not measured" in out["source"], True)
    check("calories carried through", out["nutrients"]["calories"]["value"], 52.0)
    check("a field the model didn't return is simply absent",
         "fiber_g" not in out["nutrients"], True)

    async def not_food(system, user, **kw):
        return {"is_food": False}

    llm.chat_json = not_food
    try:
        out = asyncio.run(nutrition.llm_guess("a stand mixer"))
    finally:
        llm.chat_json = real
    check("something that isn't food returns nothing, not a fake guess",
         out, None)

    async def fails(system, user, **kw):
        raise llm.LLMError("no route to the model")

    llm.chat_json = fails
    try:
        out = asyncio.run(nutrition.llm_guess("flour"))
    finally:
        llm.chat_json = real
    check("a model failure returns None rather than raising", out, None)

    check("blank name is never sent to the model",
         asyncio.run(nutrition.llm_guess("   ")), None)


# ---------------------------------------------------------------------------
def test_suggest_tools_only_when_the_page_published_nothing():
    """Same shape as nutrition.llm_guess(): a model fallback used only
    because most source pages never publish schema.org's `tool` field at
    all, pinned so it can't quietly start returning garbage."""
    import asyncio
    from app import labels, llm

    async def fake_chat_json(system, user, **kw):
        return {"tools": ["Dutch oven", "Meat thermometer", "  ", ""]}

    real = llm.chat_json
    llm.chat_json = fake_chat_json
    try:
        out = asyncio.run(labels.suggest_tools(
            {"title": "Braised short ribs", "ingredients": ["short ribs"],
             "steps": ["Sear the ribs.", "Braise for 3 hours."]}))
    finally:
        llm.chat_json = real
    check("blank entries dropped",
         out, ["Dutch oven", "Meat thermometer"])

    async def empty(system, user, **kw):
        return {"tools": []}

    llm.chat_json = empty
    try:
        out = asyncio.run(labels.suggest_tools({"title": "Toast"}))
    finally:
        llm.chat_json = real
    check("a recipe that genuinely needs nothing extra returns an empty list",
         out, [])

    async def fails(system, user, **kw):
        raise llm.LLMError("no route to the model")

    llm.chat_json = fails
    try:
        out = asyncio.run(labels.suggest_tools({"title": "Anything"}))
    finally:
        llm.chat_json = real
    check("a model failure returns [] rather than raising", out, [])

    # CAUGHT LIVE: TOOLS_SYSTEM used to list example equipment by name, and
    # llama3.2:3b copied that list back on nearly every recipe -- a shrimp
    # bake and a slow-cooker recipe both came back with the same six items,
    # the slow cooker recipe missing "slow cooker" specifically. Pinned so
    # nobody re-adds a concrete tool name as an "example" and reintroduces it.
    named = ["stand mixer", "food processor", "blender", "dutch oven",
             "meat thermometer", "slow cooker", "grill", "rolling pin",
             "springform pan"]
    lowered = labels.TOOLS_SYSTEM.lower()
    check("the prompt itself names no equipment the model could copy",
         [t for t in named if t in lowered], [])


# ---------------------------------------------------------------------------
#  Reading a step aloud (app/tts.py)
# ---------------------------------------------------------------------------
def test_tts_speech_text_strips_markdown_and_html():
    from app import tts

    check("an embedded illustration is removed, not read as gibberish",
         tts.speech_text("Fold gently. ![a drawing of this step](https://x/y.png) Rest 5 min."),
         "Fold gently. Rest 5 min.")
    check("a markdown link keeps its words, drops the address",
         tts.speech_text("See [the full method](https://example.com/page) for detail."),
         "See the full method for detail.")
    check("stray HTML tags are stripped",
         tts.speech_text("Preheat the oven <br>to 180C."), "Preheat the oven to 180C.")
    check("emphasis markers are stripped, the words stay",
         tts.speech_text("**Whisk** until _smooth_."), "Whisk until smooth.")
    check("collapses whitespace left behind", tts.speech_text("a   b\n\nc"), "a b c")
    check("empty input is empty output", tts.speech_text(""), "")


def test_tts_enabled_reflects_the_configured_voice_name():
    from app import tts

    real = tts.VOICE_NAME
    try:
        tts.VOICE_NAME = ""
        check("no voice name configured -> disabled", tts.enabled(), False)
        tts.VOICE_NAME = "june"
        check("a voice name configured -> enabled", tts.enabled(), True)
    finally:
        tts.VOICE_NAME = real


def test_tts_speak_without_a_voice_name_never_reaches_the_network():
    import asyncio
    from app import tts

    real = tts.VOICE_NAME
    tts.VOICE_NAME = ""
    real_client = tts.httpx.AsyncClient

    def boom(*a, **k):
        raise AssertionError("must not touch the network with no voice name set")
    tts.httpx.AsyncClient = boom
    try:
        try:
            asyncio.run(tts.speak("Whisk the eggs until pale."))
            check("raises rather than returning silently", False, True)
        except tts.TTSError:
            pass
    finally:
        tts.VOICE_NAME = real
        tts.httpx.AsyncClient = real_client


class _FakeTTSResponse:
    def __init__(self, content=b"", headers=None, status=200):
        self.content = content
        self.headers = headers or {}
        self.status_code = status

    def raise_for_status(self):
        # 400 never reaches here -- tts.speak() checks it explicitly and
        # raises its own TTSError before ever calling this.
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


def _fake_tts_client(resp):
    """One FakeClient class bound to a single canned POST /v1/audio/speech
    answer -- voice/server.py's own endpoint is one request, no polling,
    unlike the voicebox studio this used to call."""
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            return resp

    return FakeClient


def test_tts_speak_happy_path_returns_audio():
    import asyncio
    from app import tts

    real_name, real_client = tts.VOICE_NAME, tts.httpx.AsyncClient
    tts.VOICE_NAME = "june"
    try:
        tts.httpx.AsyncClient = _fake_tts_client(
            _FakeTTSResponse(content=b"ID3....mp3bytes", headers={"content-type": "audio/mpeg"}))
        audio, ctype = asyncio.run(tts.speak("Whisk the eggs until pale."))
        check("audio bytes came back", audio, b"ID3....mp3bytes")
        check("content type came from voice's own response", ctype, "audio/mpeg")
    finally:
        tts.VOICE_NAME, tts.httpx.AsyncClient = real_name, real_client


def test_tts_speak_no_clip_for_this_voice_is_a_plain_error():
    import asyncio
    from app import tts

    real_name, real_client = tts.VOICE_NAME, tts.httpx.AsyncClient
    tts.VOICE_NAME = "nobody"
    try:
        tts.httpx.AsyncClient = _fake_tts_client(_FakeTTSResponse(status=400))
        try:
            asyncio.run(tts.speak("Whisk the eggs until pale."))
            check("raises rather than crashing on a 400", False, True)
        except tts.TTSError as e:
            check("points at where to drop the clip",
                 "voices/nobody" in str(e), True)
    finally:
        tts.VOICE_NAME, tts.httpx.AsyncClient = real_name, real_client


def test_tts_speak_network_failure_is_a_plain_error_not_a_crash():
    import asyncio
    from app import tts

    real_name, real_client = tts.VOICE_NAME, tts.httpx.AsyncClient
    tts.VOICE_NAME = "june"

    def boom(*a, **k):
        raise ConnectionError("no route to host")
    tts.httpx.AsyncClient = boom
    try:
        try:
            asyncio.run(tts.speak("Whisk the eggs until pale."))
            check("raises rather than letting a raw ConnectionError escape", False, True)
        except tts.TTSError:
            pass
    finally:
        tts.VOICE_NAME, tts.httpx.AsyncClient = real_name, real_client


def test_tts_cached_speak_writes_once_and_reuses_the_file():
    import asyncio
    import tempfile
    from pathlib import Path
    from app import tts

    real_name, real_client, real_dir = tts.VOICE_NAME, tts.httpx.AsyncClient, tts.AUDIO_DIR
    tts.VOICE_NAME = "june"
    calls = []

    def counting_client(*a, **k):
        calls.append(1)
        return _fake_tts_client(
            _FakeTTSResponse(content=b"mp3bytes", headers={"content-type": "audio/mpeg"}))(*a, **k)

    with tempfile.TemporaryDirectory() as td:
        tts.AUDIO_DIR = Path(td)
        tts.httpx.AsyncClient = counting_client
        try:
            a1, c1 = asyncio.run(tts.cached_speak("draft1", "step-1", "Whisk the eggs."))
            a2, c2 = asyncio.run(tts.cached_speak("draft1", "step-1", "Whisk the eggs."))
            check("network hit exactly once, not twice", len(calls), 1)
            check("both calls returned the same audio", a1, a2)
            check("both calls agree on content type", c1, c2)
            saved = list((tts.AUDIO_DIR / "draft1").glob("step-1-*.mp3"))
            check("exactly one file saved for this slot", len(saved), 1)
        finally:
            tts.VOICE_NAME, tts.httpx.AsyncClient, tts.AUDIO_DIR = real_name, real_client, real_dir


def test_tts_cached_speak_replaces_a_stale_file_for_the_same_slot():
    import asyncio
    import tempfile
    from pathlib import Path
    from app import tts

    real_name, real_client, real_dir = tts.VOICE_NAME, tts.httpx.AsyncClient, tts.AUDIO_DIR
    tts.VOICE_NAME = "june"
    with tempfile.TemporaryDirectory() as td:
        tts.AUDIO_DIR = Path(td)
        draft_dir = tts.AUDIO_DIR / "draft1"
        draft_dir.mkdir(parents=True)
        (draft_dir / "step-1-oldkeyfromapriorwording.mp3").write_bytes(b"stale")
        tts.httpx.AsyncClient = _fake_tts_client(
            _FakeTTSResponse(content=b"fresh mp3", headers={"content-type": "audio/mpeg"}))
        try:
            asyncio.run(tts.cached_speak("draft1", "step-1", "A totally different sentence now."))
            saved = list(draft_dir.glob("step-1-*.mp3"))
            check("the old cached file for this slot is gone, not stacked",
                 len(saved), 1)
            check("what's left is the fresh one, not the stale one",
                 saved[0].read_bytes(), b"fresh mp3")
        finally:
            tts.VOICE_NAME, tts.httpx.AsyncClient, tts.AUDIO_DIR = real_name, real_client, real_dir


# ---------------------------------------------------------------------------
#  Pictures for foods and tools
# ---------------------------------------------------------------------------
def test_foodimages_search_images_parses_results_and_needs_a_vqd():
    import asyncio
    from app import foodimages

    class _FakeResp:
        def __init__(self, text="", json_body=None, status=200):
            self.text = text
            self._json = json_body
            self.status_code = status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"status {self.status_code}")

        def json(self):
            return self._json

    class _FakeClient:
        def __init__(self, html_resp, img_resp):
            self._html = html_resp
            self._img = img_resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            return self._html if "html.duckduckgo.com" in url else self._img

    real = foodimages.httpx.AsyncClient
    try:
        html = _FakeResp(text='blah <input type="hidden" name="vqd" value="abc123"> blah')
        img = _FakeResp(json_body={"results": [
            {"image": "https://ex.test/onion.jpg", "thumbnail": "https://ex.test/onion-t.jpg",
             "title": "A yellow onion", "url": "https://ex.test/page"},
            {"image": "", "thumbnail": "", "title": "no image, dropped"},
        ]})
        foodimages.httpx.AsyncClient = lambda **kw: _FakeClient(html, img)
        out = asyncio.run(foodimages.search_images("onion"))
        check("the imageless candidate is dropped, only one kept", len(out), 1)
        check("url carried through", out[0]["url"], "https://ex.test/onion.jpg")
        check("title carried through", out[0]["title"], "A yellow onion")

        # No vqd anywhere on the page.
        no_vqd = _FakeResp(text="<html>nothing here</html>")
        foodimages.httpx.AsyncClient = lambda **kw: _FakeClient(no_vqd, img)
        try:
            asyncio.run(foodimages.search_images("onion"))
            check("a missing vqd should have raised", True, False)
        except foodimages.ImageSearchError:
            pass

        # A real vqd but nothing usable came back.
        empty = _FakeResp(json_body={"results": []})
        foodimages.httpx.AsyncClient = lambda **kw: _FakeClient(html, empty)
        try:
            asyncio.run(foodimages.search_images("onion"))
            check("zero results should raise, not return []", True, False)
        except foodimages.ImageSearchError:
            pass

        # An empty query never even makes a request.
        try:
            asyncio.run(foodimages.search_images("   "))
            check("an empty query should have raised", True, False)
        except foodimages.ImageSearchError:
            pass
    finally:
        foodimages.httpx.AsyncClient = real


def test_foodimages_download_image_checks_status_and_size():
    import asyncio
    from app import foodimages

    class _FakeResp:
        def __init__(self, status=200, content=b""):
            self.status_code = status
            self.content = content

    class _FakeClient:
        def __init__(self, resp):
            self._resp = resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return self._resp

    real = foodimages.httpx.AsyncClient
    try:
        foodimages.httpx.AsyncClient = lambda **kw: _FakeClient(_FakeResp(status=404))
        try:
            asyncio.run(foodimages.download_image("https://ex.test/x.jpg"))
            check("a 404 should have raised", True, False)
        except foodimages.ImageSearchError:
            pass

        too_big = b"x" * (8 * 1024 * 1024 + 1)
        foodimages.httpx.AsyncClient = lambda **kw: _FakeClient(_FakeResp(content=too_big))
        try:
            asyncio.run(foodimages.download_image("https://ex.test/x.jpg"))
            check("an oversized picture should have raised", True, False)
        except foodimages.ImageSearchError:
            pass

        foodimages.httpx.AsyncClient = lambda **kw: _FakeClient(
            _FakeResp(content=b"\xff\xd8\xffjpegdata"))
        data = asyncio.run(foodimages.download_image("https://ex.test/x.jpg"))
        check("bytes pass through unchanged", data, b"\xff\xd8\xffjpegdata")
    finally:
        foodimages.httpx.AsyncClient = real


def test_foodimages_prompt_for_item_leads_with_the_item():
    from app import foodimages

    check("food prompt", foodimages.prompt_for_item("Yellow Onion"),
         "yellow onion, a single ingredient, plain background, photograph, natural light")
    check("tool prompt uses the given kind",
         foodimages.prompt_for_item("Dutch Oven", "kitchen tool"),
         "dutch oven, a single kitchen tool, plain background, photograph, natural light")


def test_mealie_food_image_round_trips_through_extras_as_a_json_string():
    from app import mealie

    check("no extras at all", mealie.food_image({"extras": {}}), None)
    check("extras present but no image key",
         mealie.food_image({"extras": {"nutrition": "{}"}}), None)
    check("garbage JSON in the field is tolerated, not raised",
         mealie.food_image({"extras": {"image": "not json"}}), None)

    food = {"id": "f1", "name": "Onion", "extras": {
        "image": json.dumps({"url": "/kitchen/food-image/f1/photo.jpg",
                             "source": "internet", "alt": "An onion"})}}
    check("decodes the saved shape", mealie.food_image(food),
         {"url": "/kitchen/food-image/f1/photo.jpg", "source": "internet", "alt": "An onion"})


def test_mealie_set_food_image_does_a_full_put_with_extras_as_a_string():
    """CAUGHT LIVE, see _food_baseline's own docstring: Mealie stores each
    extras value as its own JSON string, not a nested dict -- a dict there
    400s. set_food_image must keep that shape, and it must PUT the whole
    food back rather than a partial patch."""
    import asyncio
    from app import mealie

    seen = {}

    class _FakeResp:
        status_code = 200

        def json(self):
            return {}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def put(self, url, json=None, headers=None):
            seen["url"] = url
            seen["json"] = json
            return _FakeResp()

    real = mealie.httpx.AsyncClient
    mealie.httpx.AsyncClient = lambda **kw: _FakeClient()
    try:
        food = {"id": "f1", "name": "Onion", "extras": {"nutrition": "{}"}}
        asyncio.run(mealie.set_food_image(
            food, "/kitchen/food-image/f1/photo.jpg", "internet", "An onion"))
        check("PUT hit this food's own id", seen["url"].endswith("/api/foods/f1"), True)
        check("the rest of the food travelled through untouched",
             seen["json"]["name"], "Onion")
        check("the existing extras key survived alongside the new one",
             seen["json"]["extras"]["nutrition"], "{}")
        check("extras.image is a JSON STRING, not a nested dict",
             isinstance(seen["json"]["extras"]["image"], str), True)
        check("and it decodes back to what was asked for",
             json.loads(seen["json"]["extras"]["image"]),
             {"url": "/kitchen/food-image/f1/photo.jpg", "source": "internet",
              "alt": "An onion"})
    finally:
        mealie.httpx.AsyncClient = real


def test_mealie_get_food_and_get_tool_return_none_rather_than_raise():
    import asyncio
    from app import mealie

    class _FakeResp:
        def __init__(self, status=200, body=None):
            self.status_code = status
            self._body = body or {}
            # _get() reads r.text[:200] to build its error message on a
            # non-2xx status -- a real httpx.Response always has it, so the
            # fake needs it too or the 404 case below raises the wrong thing.
            self.text = f"status {status}"

        def json(self):
            return self._body

    class _FakeClient:
        def __init__(self, resp):
            self._resp = resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return self._resp

    real_client = mealie.httpx.AsyncClient
    real_token = mealie.core.MEALIE_TOKEN
    mealie.core.MEALIE_TOKEN = "test-token"
    try:
        mealie.httpx.AsyncClient = lambda **kw: _FakeClient(_FakeResp(status=404))
        check("a missing food is None, not a raise",
             asyncio.run(mealie.get_food("nope")), None)
        check("a missing tool is None, not a raise",
             asyncio.run(mealie.get_tool("nope")), None)

        mealie.httpx.AsyncClient = lambda **kw: _FakeClient(
            _FakeResp(body={"id": "f1", "name": "Onion"}))
        check("a found food comes through",
             asyncio.run(mealie.get_food("f1")), {"id": "f1", "name": "Onion"})
    finally:
        mealie.httpx.AsyncClient = real_client
        mealie.core.MEALIE_TOKEN = real_token


def test_foodphotos_save_detects_kind_and_keeps_one_file_per_food():
    import tempfile
    from app import foodphotos

    fid = "11111"
    with tempfile.TemporaryDirectory() as tmp:
        old = foodphotos.FOOD_PHOTO_DIR
        foodphotos.FOOD_PHOTO_DIR = pathlib.Path(tmp)
        try:
            name = foodphotos.save(fid, b"\xff\xd8\xff" + b"jpegbytes")
            check("jpeg magic number detected", name, "photo.jpg")
            p = foodphotos.path(fid, name)
            check("saved bytes read back unchanged", p.read_bytes(),
                 b"\xff\xd8\xff" + b"jpegbytes")

            # A second save with a different format REPLACES the old file --
            # one picture per food, not an accumulation.
            name2 = foodphotos.save(fid, b"\x89PNG\r\n\x1a\n" + b"pngbytes")
            check("second save switches extension", name2, "photo.png")
            check("the old jpg is gone", foodphotos.path(fid, "photo.jpg"), None)
        finally:
            foodphotos.FOOD_PHOTO_DIR = old


def test_foodphotos_rejects_junk_bytes_bad_ids_and_bad_names():
    import tempfile
    from app import foodphotos

    with tempfile.TemporaryDirectory() as tmp:
        old = foodphotos.FOOD_PHOTO_DIR
        foodphotos.FOOD_PHOTO_DIR = pathlib.Path(tmp)
        try:
            try:
                foodphotos.save("11111",
                                b"not a picture at all")
                check("junk bytes should have been refused", True, False)
            except foodphotos.FoodPhotoError:
                pass

            try:
                foodphotos.save("../../etc/passwd", b"\xff\xd8\xff" + b"x")
                check("a non-uuid id should have been refused", True, False)
            except foodphotos.FoodPhotoError:
                pass

            check("path() refuses a name outside the fixed pattern",
                 foodphotos.path("11111",
                                 "../../secret"), None)
        finally:
            foodphotos.FOOD_PHOTO_DIR = old


def test_foodphotos_keep_drawn_requires_a_real_png():
    import tempfile
    from app import foodphotos

    fid = "22222"
    with tempfile.TemporaryDirectory() as tmp:
        old = foodphotos.FOOD_PHOTO_DIR
        foodphotos.FOOD_PHOTO_DIR = pathlib.Path(tmp)
        try:
            try:
                foodphotos.save(fid, b"not a png", drawn=True)
                check("a fake PNG should have been refused", True, False)
            except foodphotos.FoodPhotoError:
                pass

            name = foodphotos.save(fid, b"\x89PNG\r\n\x1a\n" + b"realpngbytes", drawn=True)
            check("a real PNG is accepted and prefixed so it can never be "
                 "mistaken for a real photo", name, "drawn.png")
        finally:
            foodphotos.FOOD_PHOTO_DIR = old


def test_toolimages_cache_round_trips_and_is_the_dedupe_check():
    """toolimages.py's JSON cache stands in for Mealie's extras, which a
    RecipeTool does not have. Jason's own rule ("if it finds a Large mixing
    bowl once don't go find it again") lives entirely in this file being
    checked BEFORE a search or a draw is ever attempted -- this test is
    that contract, not just a read/write round trip."""
    import tempfile
    from app import toolimages

    tid = "33333"
    with tempfile.TemporaryDirectory() as tmp:
        old_file = toolimages.TOOL_IMAGE_FILE
        toolimages.TOOL_IMAGE_FILE = pathlib.Path(tmp) / "tool-images.json"
        try:
            check("nothing saved yet", toolimages.get(tid), None)
            toolimages.set(tid, "Dutch Oven", "/kitchen/tool-image/x/photo.jpg",
                           "internet", "A Dutch oven")
            check("saved shape round-trips", toolimages.get(tid),
                 {"name": "Dutch Oven", "url": "/kitchen/tool-image/x/photo.jpg",
                  "source": "internet", "alt": "A Dutch oven"})
            check("a second lookup of the SAME tool sees it already has one "
                 "-- this is what stops a second recipe re-fetching it",
                 tid in toolimages.all_with_images(), True)

            toolimages.set(tid, "Dutch Oven", "/kitchen/tool-image/x/drawn.png",
                           "ai-drawn", "AI-drawn, not a real photo")
            check("re-setting overwrites rather than accumulating a second entry",
                 len(toolimages.all_with_images()), 1)
            check("and the new source sticks", toolimages.get(tid)["source"], "ai-drawn")
        finally:
            toolimages.TOOL_IMAGE_FILE = old_file


def test_toolimages_photo_storage_same_rules_as_foodphotos():
    import tempfile
    from app import toolimages

    tid = "44444"
    with tempfile.TemporaryDirectory() as tmp:
        old = toolimages.TOOL_PHOTO_DIR
        toolimages.TOOL_PHOTO_DIR = pathlib.Path(tmp)
        try:
            name = toolimages.save(tid, b"\xff\xd8\xff" + b"jpegbytes")
            check("jpeg detected", name, "photo.jpg")
            try:
                toolimages.save("not-a-uuid", b"\xff\xd8\xff" + b"x")
                check("a bad id should have been refused", True, False)
            except toolimages.ToolPhotoError:
                pass
        finally:
            toolimages.TOOL_PHOTO_DIR = old


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED\n")
        for f in FAILURES:
            print("  * " + f)
        return 1
    print(f"all {len(tests)} test groups passed")
    return 0



# ---------------------------------------------------------------------------
def test_ing_clean_strips_scrape_junk_not_real_punctuation():
    """Trademark symbols and the {alt ingredient} bracket notation some
    recipe sites use are noise from the scrape, not something to keep --
    but a natural adjective-list comma or a parenthetical that actually
    means something ("(dried)") is real punctuation and stays."""
    assert (ing_clean.clean("1 teaspoon McCormick® Basil Leaves")
            == "1 teaspoon McCormick Basil Leaves")
    assert (ing_clean.clean("1 cup heavy cream {light cream}")
            == "1 cup heavy cream (or light cream)")
    assert (ing_clean.clean("2 tablespoons water { white wine }")
            == "2 tablespoons water (or white wine)")
    assert ing_clean.clean("wing.") == "wing"
    assert (ing_clean.clean("skin-on, bone-in chicken thighs")
            == "skin-on, bone-in chicken thighs")
    assert (ing_clean.clean("oregano (dried)") == "oregano (dried)")
    assert (ing_clean.clean("powdered sugar (confectioners' sugar)")
            == "powdered sugar (confectioners' sugar)")
    assert ing_clean.clean("") == ""
    assert ing_clean.clean(None) is None


def test_ing_clean_drops_a_sites_metric_imperial_duplicate():
    """CAUGHT LIVE: a real recipe's own metric/imperial toggle joined
    into one string by the scrape ("1.5kg / 3lb (8) yellow peaches")
    confused Mealie's own parser badly enough to turn a different line's
    "1 tsp" into "1 Tbsps" on the same import. Imperial wins, since every
    other measurement already in this library is imperial. NOT the same
    shape as a genuine name alternative ("caster / superfine sugar") --
    neither side of that one starts with a number, so it is never
    touched."""
    assert (ing_clean.clean("1.5kg / 3lb (8) yellow peaches")
            == "3 lb (8) yellow peaches")
    assert (ing_clean.clean("85g / 6 tbsp unsalted butter")
            == "6 tbsp unsalted butter")
    assert (ing_clean.clean("2 tbsp / 30ml olive oil")
            == "2 tbsp olive oil")
    assert (ing_clean.clean("1/4 cup caster / superfine sugar")
            == "1/4 cup caster / superfine sugar")
    assert (ing_clean.clean("1 tsp cornflour / cornstarch")
            == "1 tsp cornflour / cornstarch")


def test_ing_clean_fixes_paren_artifacts_without_ever_unbalancing_them():
    """A leading comma inside a parenthetical, a footnote reference that
    points at nothing once off the source page, and a TRUE double-wrap
    ("((text))" with nothing else nested inside) all get cleaned up. A
    paren that legitimately wraps more than one thing -- "((ripe and
    juicy!), peeled, cut into wedges)" -- is left alone rather than
    naively merged, which would leave a dangling unmatched paren behind.
    Every case checked for balance, not just content, since an unbalanced
    result is worse than an unfixed one."""
    cases = [
        "10 bone-in skinless chicken thighs (, about 5 ounces each, trimmed)",
        "1 1/4 cups flour (, plain/all purpose)",
        "1 tsp demerara sugar ((Note 3))",
        "1 tbsp lemon juice ((plus more as desired))",
        "1/2 tsp baking soda (bi-carb) ((or extra 1 1/2 tsp baking powder))",
        "1.5kg / 3lb (8) yellow peaches ((ripe and juicy!), peeled, "
        "stone removed, cut into eight wedges (Note 1))",
    ]
    for c in cases:
        out = ing_clean.clean(c)
        assert out.count("(") == out.count(")"), (c, out)
    assert (ing_clean.clean("1 tsp demerara sugar ((Note 3))")
            == "1 tsp demerara sugar")
    assert (ing_clean.clean("1 tbsp lemon juice ((plus more as desired))")
            == "1 tbsp lemon juice (plus more as desired)")
    assert (ing_clean.clean("10 bone-in skinless chicken thighs (, about 5 ounces each, trimmed)")
            == "10 bone-in skinless chicken thighs (about 5 ounces each, trimmed)")


def test_crawl_cleans_ingredients_and_tools_from_scraped_json_ld():
    """The cleanup has to actually run during import, not just exist --
    otherwise every NEW recipe brings the same McCormick(R)/{swap} junk
    right back in."""
    reader = crawl.Reader.__new__(crawl.Reader)
    reader.ld = [json.dumps({
        "@type": "Recipe",
        "name": "Test Bake",
        "recipeIngredient": ["1 teaspoon McCormick® Basil Leaves",
                              "1 cup heavy cream {light cream}"],
        "recipeInstructions": ["Mix.", "Bake."],
        "tool": ["McCormick® Grill Mates® Basting Brush"],
    })]
    reader.meta = {}
    recipe = crawl.from_jsonld(reader, "https://example.com/r")
    assert recipe["ingredients"] == [
        "1 teaspoon McCormick Basil Leaves",
        "1 cup heavy cream (or light cream)",
    ], recipe["ingredients"]
    assert recipe["tools"] == ["McCormick Grill Mates Basting Brush"], recipe["tools"]


# ---------------------------------------------------------------------------
def test_recipe_requests_store():
    """A request gets a real id, sits unfulfilled until a draft claims it,
    and a bad/stale id fulfilling nothing is simply a no-op rather than a
    crash -- own_save() in main.py calls request_fulfill() unconditionally
    after a save has already succeeded, so it must never raise."""
    import tempfile, os
    from app import core, store
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    old = core.DB_PATH
    core.DB_PATH = pathlib.Path(path)
    try:
        store.init()
        req_id = store.request_add("Mama's Fried Chicken",
                                   "The one with the buttermilk soak",
                                   "person6@example.com", "person7@example.com")
        assert len(req_id) == 12, req_id

        got = store.request_get(req_id)
        assert got["title"] == "Mama's Fried Chicken", got
        assert got["for_email"] == "person6@example.com", got          # lowercased
        assert got["fulfilled"] == 0, got
        assert got["draft_id"] == "", got

        assert store.request_get("no-such-id") is None

        all_rows = store.requests_all()
        assert len(all_rows) == 1 and all_rows[0]["id"] == req_id, all_rows

        store.request_fulfill(req_id, "d123456789ab")
        got = store.request_get(req_id)
        assert got["draft_id"] == "d123456789ab", got
        assert got["fulfilled"] > 0, got

        # A bad id -- the row was deleted, or never existed -- must not raise.
        store.request_fulfill("totally-not-real", "someid")
    finally:
        core.DB_PATH = old
        os.unlink(path)


def test_votes():
    """A thumb is one per person, and pressing it again takes it back."""
    import tempfile, os
    from app import core, store
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    old = core.DB_PATH
    core.DB_PATH = pathlib.Path(path)
    try:
        store.init()
        store.vote("r1", "person6@example.com", 1)
        # The same person voting again does NOT add a second vote -- the
        # primary key is what enforces that, not a read-then-write.
        store.vote("r1", "person6@example.com", 1)
        v = store.votes_for("r1", "person6@example.com")
        assert v == {"up": 0, "down": 0, "mine": 0}, v      # toggled back off

        store.vote("r1", "person6@example.com", 1)
        store.vote("r1", "person8@example.com", 1)
        store.vote("r1", "person7@example.com", -1)
        v = store.votes_for("r1", "person7@example.com")
        assert (v["up"], v["down"], v["mine"]) == (2, 1, -1), v

        # Changing your mind swaps the vote rather than counting twice.
        store.vote("r1", "person8@example.com", -1)
        v = store.votes_for("r1")
        assert (v["up"], v["down"]) == (1, 2), v

        counts = store.vote_counts()
        assert counts["r1"] == {"up": 1, "down": 2}, counts
    finally:
        core.DB_PATH = old
        # WAL leaves two sidecar files; a temp dir full of kitchen-*.db-wal is
        # how a test suite quietly fills a disk.
        for extra in ("", "-wal", "-shm"):
            try:
                os.unlink(path + extra)
            except OSError:
                pass


def test_comments():
    """Notes belong to whoever wrote them; only they or the owner remove one."""
    import tempfile, os
    from app import core, store
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    old = core.DB_PATH
    core.DB_PATH = pathlib.Path(path)
    try:
        store.init()
        assert store.add_comment("r1", "person6@example.com", "Mama", "Needed longer.")
        assert not store.add_comment("r1", "person6@example.com", "Mama", "   ")
        notes = store.comments_for("r1")
        assert len(notes) == 1 and notes[0]["who"] == "Mama"
        assert notes[0]["email"] == "person6@example.com"       # stored folded
        assert notes[0]["when"]                        # rendered for the page

        cid = notes[0]["id"]
        assert not store.drop_comment(cid, "person8@example.com")        # not yours
        assert store.drop_comment(cid, "person8@example.com", True)      # unless owner
        assert store.comments_for("r1") == []
    finally:
        core.DB_PATH = old
        # WAL leaves two sidecar files; a temp dir full of kitchen-*.db-wal is
        # how a test suite quietly fills a disk.
        for extra in ("", "-wal", "-shm"):
            try:
                os.unlink(path + extra)
            except OSError:
                pass



# ---------------------------------------------------------------------------
def test_spoken_quantities():
    """"half a cup" is one quantity, not the word half next to the word a.

    Pinned because getting this wrong was invisible: the page printed
    "1 a cup of sugar", which reads as a typo rather than as a wrong number,
    and "half a stick of butter" scaled to halves instead of eighths and came
    out a third short with nothing to show for it.
    """
    def got(text, factor):
        return scale.line(text, factor)[0]

    assert got("half a cup of sugar", Fraction(2)) == "1 cup of sugar"
    assert got("half an onion", Fraction(2)) == "1 onion"
    assert got("half a teaspoon of salt", Fraction(3)) == "1 1/2 teaspoon of salt"
    # Measurement rounding, not count rounding -- the article must be out of
    # the way before the unit test runs or this comes back "1/2".
    assert got("half a stick of butter", Fraction(11, 8)) == "3/4 stick of butter"

    # "a quarter of an onion": the fraction is the amount, the "a" is grammar.
    assert got("a quarter of an onion", Fraction(2)) == "1/2 onion"
    assert got("a quarter of a cup of milk", Fraction(2)) == "1/2 cup of milk"

    # Proportions of the recipe itself do not scale: in a doubled recipe,
    # half the butter is still half of it.
    assert scale.line("half the butter", Fraction(2)) == ("half the butter", False)

    # Vague amounts stay vague, with or without a number word in front.
    assert scale.line("a pinch of salt", Fraction(2))[1] is False
    assert scale.line("half a pinch of salt", Fraction(2))[1] is False
    assert scale.line("a handful of parsley", Fraction(2))[1] is False

    # And none of this disturbed what already worked.
    assert got("500g flour", Fraction(2)) == "1000g flour"
    assert got("two onions", Fraction(2)) == "4 onions"
    assert got("a cup of sugar", Fraction(5, 8)) == "5/8 cup of sugar"


def test_person_pills_are_not_snappable():
    """A model can never be handed somebody's made-and-tested pill.

    snap() matches a bare word inside a longer pill, so while the person pills
    were in the vocabulary the label "mama" -- which is what a model emits for
    "Mama's Cornbread" -- snapped onto "mama made and tested" and the app
    claimed she had cooked something she had never seen. The filter in
    suggest() could not catch it because the pill was created BY the snap,
    after that filter ran.
    """
    # The pill NAMES are what matters here, not who maps to them, so this does
    # not touch labels.COOKS -- a test that mutates it leaks into every test
    # that runs after it, and the order tests run in is alphabetical, not
    # chosen.
    used = [(f"mama{labels.MADE_SUFFIX}", 12), (f"casey{labels.ADDED_SUFFIX}", 5),
            ("chicken", 9), ("cornbread", 4)]
    vocab = labels.vocabulary(used)
    assert not [n for n, _ in vocab if labels.is_person_pill(n)]

    for probe in (["mama"], ["made"], ["tested"], ["casey"], ["finds"]):
        out = labels.snap(probe, vocab)
        assert not any(labels.is_person_pill(x) for x in out), (probe, out)

    # The ordinary vocabulary still works.
    assert labels.snap(["crock pot"], vocab) == ["slow-cooker"]
    assert labels.snap(["chicken"], vocab) == ["chicken"]


def test_step_prompts():
    """Prompts are built in code, and only for steps worth drawing."""
    assert not art.worth_drawing("Serve.")
    assert not art.worth_drawing("Cover and refrigerate overnight for 8 hours")
    assert not art.worth_drawing("Let it rest for ten minutes before slicing")
    assert art.worth_drawing("Season the chicken on both sides and let it sit")

    # EQUIPMENT KEEPS ITS DIAGRAM, EVEN WHEN THE STEP IS LONG. "Preheat the
    # oven to 350 degrees for twenty minutes" clears the word count easily,
    # but nothing in it is food -- diagram.py's oven schematic already says
    # it better. Food actually being combined, cooked, or served (bowl, pan,
    # boil, board, plate) still earns a drawing.
    assert not art.worth_drawing("Preheat the oven to 350 degrees for twenty minutes")
    assert not art.worth_drawing("Put the casserole dish in the fridge overnight to set")
    assert not art.worth_drawing("Line a large rimmed baking tray with parchment paper")
    assert art.worth_drawing("Fold the whipped cream gently into the chocolate mixture")
    assert art.worth_drawing("Fry the onions in the pan until golden and soft")

    p = art.prompt_for("Fry the onions for 8 minutes at 180C until soft",
                       "Chicken Stew", "soul food")
    assert "8 minutes" not in p and "180c" not in p.lower()
    assert "chicken stew" in p and "soul food" in p
    # No step number, and nothing the model renders as garbled text.
    assert "step" not in p.lower()

    # THE ACTION LEADS. Not decoration: opening with "food photograph" got a
    # plated beauty shot of the finished dish for "season the chicken on both
    # sides", because these models weight the front of the prompt hardest.
    assert p.startswith("fry the onions"), p

    # Mid-recipe is work in progress; only a serving step may look finished,
    # and only that one drops the anti-plating words.
    mid = art.prompt_for("Brown it in the butter, both sides", "Chicken", "")
    end = art.prompt_for("Serve it over rice with the gravy spooned on",
                         "Chicken", "")
    assert "cooking in progress" in mid and "plated" not in mid
    assert "the finished dish, plated" in end
    assert "plated meal" in art.negative_for("Brown it in the butter")
    assert "plated meal" not in art.negative_for("Serve it over rice")



# ---------------------------------------------------------------------------
def test_redirects_are_checked_every_hop():
    """A public site cannot redirect the fetcher onto the Docker network.

    Checking only the URL somebody typed and then following redirects means
    the guard can be walked straight past: example.com is allowed to answer
    "302, go to http://ollama:11434/api/delete", and the address bar said
    example.com the whole time.
    """
    import asyncio
    from app import core, crawl

    class FakeResponse:
        def __init__(self, status, location=""):
            self.status_code = status
            self.headers = {"location": location} if location else {}

    class FakeStream:
        def __init__(self, resp):
            self.resp = resp

        async def __aenter__(self):
            return self.resp

        async def __aexit__(self, *a):
            return False

    class FakeClient:
        """Answers every request with one redirect, to somewhere internal."""
        asked = []

        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, _method, url):
            FakeClient.asked.append(url)
            return FakeStream(FakeResponse(302, "http://ollama:11434/api/tags"))

    real = crawl.httpx.AsyncClient
    crawl.httpx.AsyncClient = FakeClient
    try:
        try:
            asyncio.run(crawl.fetch("https://example.com/a-recipe"))
        except core.Blocked:
            # Blocked either because the name is not a public address, or
            # because it does not resolve at all from wherever the tests run.
            # Which of the two does not matter: what matters is that the hop
            # went through the guard instead of round it.
            pass
        else:
            raise AssertionError("the redirect to ollama was followed")
        # It must have stopped BEFORE asking the internal host for anything.
        assert not any("ollama" in u for u in FakeClient.asked), FakeClient.asked
    finally:
        crawl.httpx.AsyncClient = real


def test_a_refused_upload_keeps_the_old_photo():
    """Too big is a normal thing to do, and must not destroy what was there.

    The obvious order -- delete the old, write the new -- loses the picture
    you took last week the moment a phone photo goes over the cap, with
    nothing on the page to say so.
    """
    import io
    import tempfile
    from app import photos

    class Upload:
        def __init__(self, data):
            self.file = io.BytesIO(data)

    jpeg = bytes.fromhex("ffd8ff") + b"x" * 500      # a JPEG's magic number
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = photos.PHOTO_DIR
        photos.PHOTO_DIR = pathlib.Path(tmp)
        old_max = photos.MAX_BYTES
        try:
            name = photos.save("abc123def456", "hero", Upload(jpeg))
            good = photos.path("abc123def456", name)
            assert good and good.read_bytes() == jpeg

            photos.MAX_BYTES = 100          # the next one cannot fit
            try:
                photos.save("abc123def456", "hero", Upload(jpeg))
            except photos.PhotoError as e:
                assert "left alone" in str(e), e
            else:
                raise AssertionError("the oversized upload was accepted")

            # The good photo is still there, still whole, still the one the
            # draft points at -- and no half-written leftovers.
            assert good.is_file() and good.read_bytes() == jpeg
            assert not list(pathlib.Path(tmp).glob("**/.*.part"))
        finally:
            photos.PHOTO_DIR = old_dir
            photos.MAX_BYTES = old_max



# ---------------------------------------------------------------------------
def test_keep_records_who_and_needs_no_approval():
    """Keep goes straight into the library and says who pressed it.

    There used to be an approval queue here: a non-owner's Keep set the draft
    to 'proposed' and waited for the owner. That gate is gone -- everybody in
    /admin/people is already someone Jason let in the door, which is the real
    gate, enforced once at sign-in. mark_kept() now has to remember who did
    it, since nothing downstream can ask an approver any more.
    """
    import tempfile, os
    from app import core, store

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    old = core.DB_PATH
    core.DB_PATH = pathlib.Path(path)
    try:
        store.init()
        draft_id = store.save({"title": "Mama's smothered chicken"},
                              who="person3@example.com")
        before = store.get(draft_id)
        assert before["mealie_slug"] == "" and before["kept_by"] == ""

        store.mark_kept(draft_id, "mama-s-smothered-chicken", "person3@example.com")
        after = store.get(draft_id)
        assert after["mealie_slug"] == "mama-s-smothered-chicken"
        # Folded to lowercase, same as who_email -- a card that later looks up
        # a pill or a display name by this address must not miss because of
        # capitalisation nobody typed on purpose.
        assert after["kept_by"] == "person3@example.com"

        cards = store.cards(ids=[draft_id])
        assert cards[0]["kept_by"] == "person3@example.com"
    finally:
        core.DB_PATH = old
        for extra in ("", "-wal", "-shm"):
            try:
                os.unlink(path + extra)
            except OSError:
                pass


def test_friendly_name_uses_the_cook_map():
    """Attribution says 'mama', not 'person3' -- the name /admin/people
    would not recognise but every pill on this app already uses."""
    labels.COOKS.update({"person3@example.com": "mama"})
    assert labels.friendly_name("person3@example.com") == "mama"
    assert labels.friendly_name("person3@example.com") == "mama"          # folded
    assert labels.friendly_name("person5@example.com") == "auntjune"   # unknown, still a name
    assert labels.friendly_name("") == "someone"



# ---------------------------------------------------------------------------
def test_write_access_is_the_household_not_the_sign_in_list():
    """Everyone in /admin/people can read; only the household can change
    anything.

    THESE ARE TWO SEPARATE LISTS ON PURPOSE. people.is_allowed() answers "is
    this person let in the door" -- that already includes anyone Jason added
    for a reason that has nothing to do with cooking. people.can_write()
    answers a narrower question, and the household default has to match what
    is actually wired into KITCHEN_WRITERS in docker-compose.web.yml, or the
    two drift and the button either 403s for Mama or works for a stranger.
    """
    from app import people

    for email in ("person4@example.com", "person1@example.com",
                  "person3@example.com", "person2@example.com"):
        assert people.can_write(email), email

    for email in ("person9@example.com", "person10@example.com",
                  "a-stranger@example.com", ""):
        assert not people.can_write(email), email

    # Owners are always writers even if KITCHEN_WRITERS is set without them --
    # the same relationship OWNERS already has with is_allowed().
    assert people.OWNERS <= people.WRITERS



# ---------------------------------------------------------------------------
def test_kept_ids():
    """One query answers which of a batch's drafts already made it into the
    library -- what the Keep-all progress bar and button count are built on."""
    import tempfile, os
    from app import core, store

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    old = core.DB_PATH
    core.DB_PATH = pathlib.Path(path)
    try:
        store.init()
        a = store.save({"title": "A"}, who="mama@example.com")
        b = store.save({"title": "B"}, who="mama@example.com")
        c = store.save({"title": "C"}, who="mama@example.com")
        store.mark_kept(a, "slug-a", "mama@example.com")

        assert store.kept_ids([a, b, c]) == {a}
        # A row with no draft_id (a failed import) must not blow this up.
        assert store.kept_ids([a, b, None, ""]) == {a}
        assert store.kept_ids([]) == set()

        store.mark_kept(b, "slug-b", "mama@example.com")
        assert store.kept_ids([a, b, c]) == {a, b}
    finally:
        core.DB_PATH = old
        for extra in ("", "-wal", "-shm"):
            try:
                os.unlink(path + extra)
            except OSError:
                pass



# ---------------------------------------------------------------------------
def test_extras_shapes_the_models_answer():
    """suggest() trusts nothing about the model's JSON: a missing "add", an
    unrecognised "kind", or more than four items must not reach the page
    as-is -- and a bare string mixed into a list of objects (seen live) is
    still a usable suggestion, not a reason to drop the whole item.
    """
    import asyncio
    from app import extras, llm

    async def fake_chat_json(system, user, **kw):
        return {"suggestions": [
            {"kind": "protein", "add": "a can of black beans", "why": "bulks it out"},
            {"kind": "SPICE", "add": "smoked paprika", "why": ""},
            {"kind": "made up nonsense", "add": "chili crisp", "why": "heat"},
            {"add": ""},                       # nothing to add -- dropped
            "a squeeze of lime",               # a bare string -- still usable
            {"kind": "side", "add": "rice"},   # fifth accepted item -- capped out
        ]}

    real = llm.chat_json
    llm.chat_json = fake_chat_json
    try:
        out = asyncio.run(extras.suggest({"title": "Chili", "ingredients": []}))
    finally:
        llm.chat_json = real

    assert len(out) == 4, out
    assert out[0] == {"kind": "protein", "add": "a can of black beans",
                      "why": "bulks it out"}
    # Case-folded against the closed set, not passed through verbatim.
    assert out[1]["kind"] == "spice"
    # Not one of the five known kinds -- kept as a suggestion, just not
    # claiming a category it does not have.
    assert out[2]["kind"] == "idea"
    assert out[3] == {"kind": "idea", "add": "a squeeze of lime", "why": ""}
    # "rice" never makes it in -- the cap is on ACCEPTED suggestions, and it
    # was already reached.
    assert not any(x["add"] == "rice" for x in out)


def test_extras_tolerates_the_wrong_field_names():
    """Measured live on this box, on the SAME recipe, one call after another:
    asked for {"add": "...", "kind": "..."}, the model answered with
    "value"/"type" once and "addition"/"type" the next -- right idea, three
    different guesses at the field name across two calls. A strict
    item.get("add") turns every suggestion into an empty string and throws
    the whole answer away. Pinned so it cannot regress back to that.
    """
    import asyncio
    from app import extras, llm

    async def fake_chat_json(system, user, **kw):
        return {"suggestions": [
            {"type": "side", "value": "cornbread"},
            {"type": "spice", "addition": "pinch of cayenne pepper"},
            # Neither a recognised key nor a recognised alias -- the last
            # resort, "any other string in the item", still has to find it.
            {"category": "aromatic", "put_this_in": "diced bell pepper"},
        ]}

    real = llm.chat_json
    llm.chat_json = fake_chat_json
    try:
        out = asyncio.run(extras.suggest({"title": "Smothered chicken"}))
    finally:
        llm.chat_json = real

    assert out == [
        {"kind": "side", "add": "cornbread", "why": ""},
        {"kind": "spice", "add": "pinch of cayenne pepper", "why": ""},
        {"kind": "aromatic", "add": "diced bell pepper", "why": ""},
    ], out


def test_extras_tolerates_the_wrong_container_key():
    """Also measured live: the SAME recipe, a fourth call, wrapped the list
    in "additions" instead of "suggestions", and named the text field
    "suggestion" and the why field "description". A strict
    data["suggestions"] throws the whole answer away over the container's own
    key name, before any item is even looked at.
    """
    import asyncio
    from app import extras, llm

    async def fake_chat_json(system, user, **kw):
        return {"additions": [
            {"type": "spice", "suggestion": "Paprika",
             "description": "Adds smoky depth."},
        ]}

    real = llm.chat_json
    llm.chat_json = fake_chat_json
    try:
        out = asyncio.run(extras.suggest({"title": "Smothered chicken"}))
    finally:
        llm.chat_json = real

    assert out == [{"kind": "spice", "add": "Paprika",
                    "why": "Adds smoky depth."}], out


def test_extras_tolerates_plain_string_items():
    """Also measured live: the SAME recipe answered with a list of plain
    strings, "Spice: Paprika" style, instead of objects at all. A strict
    isinstance(item, dict) check drops every one of them.
    """
    import asyncio
    from app import extras, llm

    async def fake_chat_json(system, user, **kw):
        return {"suggestions": [
            "Spice: Paprika", "Side: Cornbread", "Herb: Thyme",
            "Aromatics: Bell Peppers",
        ]}

    real = llm.chat_json
    llm.chat_json = fake_chat_json
    try:
        out = asyncio.run(extras.suggest({"title": "Smothered chicken"}))
    finally:
        llm.chat_json = real

    assert out == [
        {"kind": "spice", "add": "Paprika", "why": ""},
        {"kind": "side", "add": "Cornbread", "why": ""},
        {"kind": "herb", "add": "Thyme", "why": ""},
        # "Aromatics" (plural) must still match the singular "aromatic" kind.
        {"kind": "aromatic", "add": "Bell Peppers", "why": ""},
    ], out

    # A plain string with no recognisable kind in front of the colon keeps
    # the whole thing rather than silently losing half of it.
    async def unlabelled(system, user, **kw):
        return {"suggestions": ["a squeeze of lime at the end"]}
    llm.chat_json = unlabelled
    try:
        out = asyncio.run(extras.suggest({"title": "Anything"}))
    finally:
        llm.chat_json = real
    assert out == [{"kind": "idea", "add": "a squeeze of lime at the end",
                   "why": ""}], out


def test_extras_finds_the_list_nested_a_level_down():
    """Also measured live: the SAME recipe wrapped its entire answer,
    suggestions included, inside a "recipe" object one level down --
    alongside an ingredients list that must NOT be mistaken for it.
    """
    import asyncio
    from app import extras, llm

    async def fake_chat_json(system, user, **kw):
        return {"recipe": {
            "name": "Mama's Smothered Chicken",
            "ingredients": ["chicken thighs", "onion", "butter"],
            "suggestions": [
                {"type": "side_dish", "addition": "cornbread"},
                {"type": "spice", "addition": "cayenne pepper"},
            ],
        }}

    real = llm.chat_json
    llm.chat_json = fake_chat_json
    try:
        out = asyncio.run(extras.suggest({"title": "Smothered chicken"}))
    finally:
        llm.chat_json = real

    # "side_dish" folds onto the real "side" kind rather than falling all
    # the way back to the generic "idea" label -- a near-miss, not a miss.
    assert out == [{"kind": "side", "add": "cornbread", "why": ""},
                   {"kind": "spice", "add": "cayenne pepper", "why": ""}], out


def test_extras_is_quiet_when_the_model_is_not_there():
    """A recipe with nothing to add and a recipe the model could not reach
    must look the same to the caller: an empty list, not an exception a
    background task would have to catch by name."""
    import asyncio
    from app import extras, llm

    async def fails(system, user, **kw):
        raise llm.LLMError("no route to the model")

    real = llm.chat_json
    llm.chat_json = fails
    try:
        out = asyncio.run(extras.suggest({"title": "Anything"}))
    finally:
        llm.chat_json = real

    assert out == []



# ---------------------------------------------------------------------------
def test_only_the_household_can_reach_the_library():
    """A read-only account must never be handed a Mealie session at all.

    Kitchen's own write gate stops them adding or changing anything through
    THIS app, but Mealie has no read-only role of its own (see the long note
    at the top of mealie.py -- a non-admin renamed a recipe and it stuck), so
    reaching Mealie's native UI directly would be a second door with no lock
    on it. The fix is not letting them through the door: /library refuses
    before handoff.mint() is ever called, for anyone people.can_write() says
    no to.
    """
    from app import people

    for email in ("person11@example.com", "person9@example.com",
                 "person10@example.com", "a-future-guest@example.com"):
        assert not people.can_write(email), email

    for email in ("person3@example.com", "person2@example.com",
                 "person4@example.com"):
        assert people.can_write(email), email


if __name__ == "__main__":
    sys.exit(main())
