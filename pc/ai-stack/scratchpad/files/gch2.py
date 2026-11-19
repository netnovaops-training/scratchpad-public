from pathlib import Path


def edit(p, pairs):
    s = Path(p).read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) == 1, (p, a[:70], s.count(a2))
        s = s.replace(a2, b2)
    Path(p).write_text(s, encoding="utf-8", newline="")

edit("CHANGES.md", [
("""| **Windows menu: More 20 and 21** | The same as Ubuntu More 18 and 19 | More 20 also copies the renamed files back to this PC's copy, so a later install from this PC does not bring the old names back |
""", """| **Windows menu: More 20 and 21** | The same as Ubuntu More 18 and 19 | More 20 also copies the renamed files back to this PC's copy, so a later install from this PC does not bring the old names back |
| **"Find it for me"** (guided installer, page 2; menu T, `F`) | Typing the garden computer's address is the hardest part for a beginner | It knocks once on port 22 of each address of this PC's home network (1.5 seconds in all; nothing logs in) and lists the computers with the SSH door open, with their names when the router knows them; one click fills the address. Page 2 also shows THIS PC's name and address, so it is clear which machine "this PC" is. Tested on a real home network: the one computer with SSH on port 22 found in 2 seconds |
| **The garden computer is found again when its address changes** (automatic; Windows More 22) | Some routers cannot reserve an address, so it can change after a restart and the menu would lose the garden computer | The menu remembers the garden computer's network card ID (MAC) and name. When nothing answers at the saved address, before each job and before the weekly copy, it finds the same network card at home and saves the new address. Tested: a wrong saved address was corrected to the real computer in 7 seconds; an unknown network card was left alone. Guide 14 also shows how to give the computer a fixed address itself, which going online still needs |
"""),
("""| 47 | The rename waits for the database |""", """| 48 | The Raspberry Pi is described as tested | Guide 00b still said "expected to work, not tested yet", although the Pi 5 test had passed (8 minutes, 0 fails, 84.5 °C) | It says tested on 64-bit Raspberry Pi OS, and that it needs good cooling (a fan) |
| 47 | The rename waits for the database |"""),
])
edit("HANDOVER-SUMMARY.md", [
("found **47 problems**", "found **48 problems**"),
("문제 47개를", "문제 48개를"),
("""- **Windows: two ways to run Docker:** Docker Desktop (the easy way, with buttons) or
  Docker inside Ubuntu (the hands-on way), chosen in the installer.
""", """- **Windows: two ways to run Docker:** Docker Desktop (the easy way, with buttons) or
  Docker inside Ubuntu (the hands-on way), chosen in the installer. The installer can
  find the garden computer at home for you ("Find it for me"), and the menu finds it
  again by its network card if its address changes.
"""),
])
print("ok")
