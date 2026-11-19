from pathlib import Path


def edit(p, pairs):
    s = Path(p).read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) == 1, (p, a[:80], s.count(a2))
        s = s.replace(a2, b2)
    Path(p).write_text(s, encoding="utf-8", newline="")

edit("CHANGES.md", [
("""| **The garden computer is found again when its address changes**""", """| **More 14 is the on / off switch** (Jason's request) | "Turn the front door off" (More 14) and the new switch did nearly the same | More 14 now opens the switch; taking the garden off the internet only is its choice 3 (`bash garden.sh offline` still does it directly). No other menu number moved. On Windows, More 15 is the switch and "find the garden computer again" is More 21 |
| **A Gmail just for the website** (guide 00b, FAQ, the email step) | One account for the website's email, Drive backups, AI key, and the outside services that offer "Sign in with Google" (Tailscale, DuckDNS, No-IP) keeps the personal inbox apart | Recommended at setup, with Google's sign-up link. The garden's own sign-in stays the garden password: no outside account can open the garden |
| **Every script that made the downloads is in the public code** (Jason's request) | Nothing about how the package or the data download was made should be hidden | `tools/release/` now holds the server export that made the data download (what was left out: the logins), the Drive upload and check, the menu pictures, and `tests/` with every test that ran. The Drive account, its link and any keys are given when a script runs; none are in the code |
| **The garden computer is found again when its address changes**"""),
("""| 47 | The rename waits for the database |""", """| 49 | Two-factor sign-in is kept | The sign-in settings file is written again by the front door, email and subdomain steps; a phone code (`two_factor`) switched on by hand (guide 07) quietly went back to password only | The file keeps `two_factor` when it is written again. Guide 07 now says to restart the sign-in page (`docker compose restart authelia`), which is what makes the change count. Tested: a new file starts with password only; `two_factor` survives a rewrite |
| 50 | The guides match the program again | A full read of every guide against the code found: the clean-up advice `docker system prune` still in guides 02 and 09 and the checklist; "Uninstall prints the commands for Caddy and No-IP" (it removes them); the Windows numbering note; the Korean guide 26's pictures pointing to the wrong folder; guide 26 missing from the index; LiteLLM, Tailscale and DuckDNS missing from the install list | All corrected, English and Korean; the checks report 0 differences between the languages |
| 47 | The rename waits for the database |"""),
("""Removes only old versions of the garden's own images older than 7 days""", """Removes only old versions of the garden's own images older than 7 days"""),
])
edit("HANDOVER-SUMMARY.md", [("found **48 problems**", "found **50 problems**"), ("문제 48개를", "문제 50개를")])
print("ok")
