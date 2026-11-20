"""Settings for the standalone /recipe app.

WHY THIS APP EXISTS
--------------------
Jason's own request (2026-08-24): a real, in-house app at /recipe with the
random-pick and multi-recipe meal-planning features he specifically said he
likes -- not a redirect out to Tandoor's own raw UI, and not just links
back into Kitchen's own pages. It replaced the earlier plan for /recipe to
be a plain redirect to Tandoor once kitshn (the real third-party Tandoor
client) came up -- this app and kitshn now both exist for different
reasons: kitshn is the phone-in-your-pocket experience, this is the
browser-on-this-domain one, and Kitchen itself stays the import/parsing/
admin backend behind both.

WHY THIS APP HAS NO TANDOOR LOGIN OF ITS OWN
-----------------------------------------------
Tandoor's session-auth quirks (a real Django session cookie, not a bearer
token for server-to-server calls -- see Kitchen's tandoor.py for the whole
story) already live in exactly one place. This app calls KITCHEN's own
/internal/recipe-app/* routes instead of touching Tandoor directly, the
same server-to-server pattern the /admin panel already uses successfully.
One less place that auth logic could drift out of sync.
"""
import os

ROOT_PATH = os.getenv("RECIPEAPP_ROOT_PATH", "/recipe")
# Jason's own request (2026-08-25): "I want the title of the page of
# /recipe to be called Jason's Recipes with a fork." SITE_NAME is the
# full name (browser tab, manifest "name"); SITE_SHORT_NAME is what
# actually shows under the icon once installed -- home screens truncate
# a long label, so it stays short rather than repeating the fork there
# too (the install icon itself is already a fork and knife).
SITE_NAME = "Jason's Recipes \U0001f374"
SITE_SHORT_NAME = "Jason's Recipes"

KITCHEN_URL = os.getenv("RECIPEAPP_KITCHEN_URL", "http://kitchen:8003")
KITCHEN_SECRET = os.getenv("KITCHEN_INTERNAL_SECRET", "")

# The BROWSER-facing path to Kitchen, not the internal docker-network URL
# above -- for the one link this app ever sends someone to Kitchen's own
# pages for (fine-tuning a just-imported draft: step pictures, tools,
# nutrition review -- everything this app's own review screen doesn't
# cover yet). See Caddyfile's own `handle_path /kitchen/*` block.
KITCHEN_PUBLIC_ROOT = os.getenv("RECIPEAPP_KITCHEN_PUBLIC_ROOT", "/kitchen")

# --- the A/B twin ------------------------------------------------------------
#
#  Jason, 2026-09-04: "let's keep both ... so compare each recipe before
#  we make the full switch." The comparison ran, and on 2026-09-06 Jason
#  called it: "make gemnie-recipe /recipe". So this is no longer a twin --
#  /recipe IS the Gemini one now, /recipe-gemini redirects to it, and the
#  second container is gone. The flag stays because turning this off is
#  how the page goes back to being exactly what it was.
#
#  The comparison is about CONTEXT, not model quality. diagram.py sees one
#  step at a time, so it cannot know a pan is already on the heat from
#  step two, or that the onion was chopped in step one. That is the thing
#  being tested, and it is why a bigger context window is the argument.
#
#  With no key this twin still runs and simply shows the local pictures,
#  labelled honestly. A comparison page that breaks when a key is missing
#  compares nothing.
GEMINI = os.getenv("RECIPEAPP_GEMINI", "").strip() in ("1", "true", "yes", "on")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL",
                               "gemini-3.1-flash-image")


def variant() -> dict:
    """What this instance is, for the badge the page wears."""
    if not GEMINI:
        return {"name": "local", "label": "local pictures",
                "note": "Step drawings from the schematic renderer, one step "
                        "at a time.", "ready": True}
    if not GEMINI_KEY:
        return {"name": "gemini", "label": "Gemini — no key yet",
                "note": "This is the Gemini twin, but GEMINI_API_KEY is empty, "
                        "so it is showing the same local pictures as /recipe. "
                        "Nothing to compare until a key is set.",
                "ready": False}
    # SAYS NOTES, BECAUSE IT IS NOTES. This used to read "Step pictures
    # drawn with the whole recipe in context" and that was not true of
    # anything on the page: /api/step-image exists but no client has ever
    # called it, so the step drawings here are the same local ones /recipe
    # has always shown. What Gemini actually contributes is the per-step
    # notes -- what to have ready, what to watch, what it should look like
    # -- and those DO see the whole recipe, which is the argument the twin
    # was set up to test. The picture route stays wired for the day
    # billing is enabled; until then it would be a promise, not a feature.
    return {"name": "gemini", "label": "Gemini notes",
            "note": "Each step also says what to have ready, what to watch "
                    "and what it should look like -- worked out with the "
                    "whole recipe in view, not one step at a time.",
            "ready": True}
