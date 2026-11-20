"""Who is allowed in — read from the one file that already decides that.

THE POINT OF THIS MODULE
------------------------
There is exactly one place people are added or removed on this machine:
`/admin/people`, which writes `Admin/auth/people.json`, which the auth service
reads on every request. That file is the front door.

Before this app existed, Mealie would have been a SECOND list — its own users,
its own passwords, its own removals to remember. Two lists that must agree, and
which will not, because the second one is only updated when somebody remembers.
The half you forget is always the removal, which is the half that matters.

So this app keeps no list of its own. It reads that file, mirrors it into
Mealie, and never writes back to it. The mount is `:ro` in compose for the same
reason: the worst a bug in here can do to your sign-in list is misread it.

WHY IT RE-READS RATHER THAN CACHES
----------------------------------
The file is a few hundred bytes and the OS has it in page cache. Reading it
per request costs nothing measurable and means removing somebody at /admin
takes effect on their next click, with no restart and no sync button to
remember. A cache with a TTL would buy nothing and add a window in which a
removed person still works.
"""
import json
import os
from pathlib import Path

PEOPLE_FILE = Path(os.getenv("KITCHEN_PEOPLE_FILE", "/people/people.json"))

# Your addresses. Owners keep recipes straight into the library; everyone else
# proposes and you approve. Defaults to the admin's own ADMIN_OWNERS list, so
# there is one answer to "who is the owner" rather than two that can drift.
OWNERS = {
    e.strip().lower()
    for e in os.getenv("KITCHEN_OWNERS", "person4@example.com,person1@example.com").split(",")
    if e.strip()
}

# Owners, plus the household. Everyone else on the sign-in list can open and
# read every page -- browse, cook from a guide, look at the shopping list --
# but cannot add a recipe, keep one, vote, leave a note, or touch the list.
#
# THIS IS A SEPARATE LIST FROM "SIGNED IN AT ALL". Being in people.json is
# what forward_auth checks, and it is what used to be the only gate this app
# had: anyone Jason added could write anywhere. That was fine when the list
# was three people who all cook here. It stopped being fine the moment two
# addresses that are not household got added for something else entirely --
# without this, they would have had exactly the same run of the kitchen as
# Mama does.
WRITERS = OWNERS | {
    e.strip().lower()
    for e in os.getenv("KITCHEN_WRITERS",
                       "person3@example.com,person2@example.com").split(",")
    if e.strip()
}


def everyone() -> list:
    """Every address on the sign-in list, lowercased. [] if unreadable.

    RETURNS EMPTY RATHER THAN RAISING, deliberately. This is called while
    rendering pages, and a missing mount or a half-written file must degrade
    to "the sync has nothing to do" rather than to a 500 on the recipe you
    were reading. Nothing here grants access -- Caddy's forward_auth already
    did that before the request arrived -- so an empty list is safe. It is
    only ever used to decide who gets a Mealie account made for them.
    """
    try:
        data = json.loads(PEOPLE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for person in data:
        if isinstance(person, dict) and isinstance(person.get("email"), str):
            email = person["email"].strip().lower()
            if email:
                out.append(email)
    return out


def is_owner(email: str) -> bool:
    return bool(email) and email.strip().lower() in OWNERS


def can_write(email: str) -> bool:
    """Owner or household. Everyone else on the list is read-only."""
    return bool(email) and email.strip().lower() in WRITERS


def is_allowed(email: str) -> bool:
    """On the list, or an owner.

    Owners are allowed even if they are not in people.json. That is not a
    back door -- forward_auth has already turned this request away if the
    person is not signed in, and the auth service's list is the same file.
    It is here so that setting KITCHEN_OWNERS to an address you have not
    added yet fails visibly at /admin rather than silently here.
    """
    return is_owner(email) or (email or "").strip().lower() in set(everyone())


def display_name(email: str) -> str:
    """'person2@example.com' -> 'person2'. For showing on a page."""
    return (email or "").split("@")[0] or "someone"
