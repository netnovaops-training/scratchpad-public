"""Break-glass commands for the recipe library.

WHY THIS FILE EXISTS
--------------------
Once the sign-in hand-off works, nobody types a Mealie password -- including
you. The app invents one per person, keeps it to itself, and replaces it
whenever it stops working. That is the point: a password nobody knows is a
password nobody can lose, reuse or be phished for.

It also means that if the auth service is broken, or people.json is wrong, or
Caddy will not start, you cannot reach the library at all -- because the only
door left is one you cannot open. That is a bad place to be with a machine in
your own house.

So: this is the way back in. It sets a Mealie password to something you choose,
using the admin API token, without needing the website to be working.

    docker compose -f docker-compose.yml -f docker-compose.web.yml `
      run --rm --entrypoint python kitchen manage.py password you@example.com

It asks for the password rather than taking it as an argument, because an
argument ends up in your shell history and in `docker ps` output.

AFTER USING IT: the next hand-off for that address will find the password no
longer matches what it has stored, and will quietly replace it again. That is
correct -- it means break-glass access is temporary by construction and does
not leave a working password lying around afterwards. Use it, do what you need
to do, and let the app take the account back.
"""
import asyncio
import getpass
import sys

from app import core, handoff, mealie, people, store


async def cmd_password(email: str) -> int:
    email = email.strip().lower()
    if not mealie.configured():
        print("KITCHEN_MEALIE_TOKEN is not set, so I cannot reach Mealie's "
              "admin API. Put the token in ai-stack/.env first.")
        return 1

    user = await handoff._find_user(email)
    if not user:
        print(f"Mealie has no user for {email}.")
        print("Addresses it does know:")
        r = await mealie._get(mealie.USERS + "?perPage=200")
        for item in r.json().get("items", []):
            print("   ", item.get("username"))
        return 1

    new = getpass.getpass(f"New Mealie password for {email}: ")
    again = getpass.getpass("Again: ")
    if new != again:
        print("Those did not match. Nothing changed.")
        return 1
    if len(new) < 8:
        print("Mealie wants at least 8 characters. Nothing changed.")
        return 1

    await handoff._reset_to(email, new)
    # Forget ours, so the next hand-off does the repair path rather than
    # trying a password we know is now wrong and looking broken for one
    # request before it fixes itself.
    store.forget_mealie_secret(email)
    print(f"Done. {email} can sign in to Mealie directly at "
          f"{core.MEALIE_PUBLIC or 'the library'} with that password.")
    print("The next time they use the website's link, the app will take the "
          "account back and this password will stop working.")
    return 0


async def cmd_who() -> int:
    """Who is on the list, who Mealie knows about, and whether they line up."""
    allowed = people.everyone()
    print(f"people.json ({people.PEOPLE_FILE}):")
    if not allowed:
        print("   nothing readable — is ../Admin/auth mounted at /people?")
    for e in allowed:
        print(f"   {e}{'   (owner)' if people.is_owner(e) else ''}")

    print("\nMealie users:")
    try:
        r = await mealie._get(mealie.USERS + "?perPage=200")
    except mealie.MealieError as e:
        print(f"   could not ask Mealie: {e}")
        return 1
    known = []
    for item in r.json().get("items", []):
        name = (item.get("username") or "").lower()
        known.append(name)
        flag = "" if name in allowed else "   <- NOT on people.json"
        print(f"   {name:30} {item.get('household', ''):8}{flag}")

    missing = [e for e in allowed if e not in known]
    if missing:
        print("\nOn the list but no Mealie account yet (made on first visit):")
        for e in missing:
            print("   " + e)
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        print("Commands:")
        print("  password <email>   set a Mealie password by hand")
        print("  who                compare people.json against Mealie's users")
        return 0
    if args[0] == "password":
        if len(args) < 2:
            print("Which address? e.g.  manage.py password you@example.com")
            return 1
        return asyncio.run(cmd_password(args[1]))
    if args[0] == "who":
        return asyncio.run(cmd_who())
    print(f"No such command: {args[0]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
