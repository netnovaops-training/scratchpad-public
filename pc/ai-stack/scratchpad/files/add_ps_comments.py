"""Insert plain-English comments above PowerShell functions that have none (comment-only)."""
import re
import sys
from pathlib import Path

C = {
 "Note": "An indented line of plain text.",
 "Warn": "A yellow WARNING line: something to fix soon, not a stop.",
 "Fail": "A red STOPPED line.",
 "Ask": "Ask a question; Enter gives the suggested answer shown in [brackets].",
 "Confirm-Step": "Ask a yes/no question. Capital letter = what Enter means.",
 "Pause-Menu": "Wait for Enter before showing the menu again, so you can read the screen.",
 "Load-Target": "Read which computer runs the garden (saved in .garden-target-windows.txt, no passwords).",
 "Save-Target": "Remember which computer runs the garden (IP and user name only, never a password).",
 "Target-Text": "Describe the garden computer in words, for the top of the menu.",
 "Choose-Target": "Menu T: choose another computer (by IP, over SSH) or this PC's built-in Ubuntu (WSL).",
 "Need-Target": "Make sure a garden computer has been chosen; ask if not.",
 "Prepare-Remote": "Before installing on another computer: check SSH answers, then add this PC's SSH key there.",
 "Prepare-Wsl": "Before installing on this PC: switch on WSL and install Ubuntu (Microsoft's own steps).",
 "Copy-Garden": "Pack the garden folder (not settings or private data) and copy it to the garden computer.",
 "Do-Install": "Menu 1: get the garden computer ready, copy the garden, run garden.sh install there.",
 "Keep-Wsl-Running": "Optional task that keeps Windows' Ubuntu running while you are signed in.",
 "Open-Terminal": "Open a terminal on the garden computer in a new window.",
 "Copy-Backup-Here": "More 10: make a backup on the garden computer and copy it to this PC.",
 "Schedule-Backup-Copy": "More 13: add or remove the weekly automatic copy of the backup to this PC.",
 "Show-Menu": "The main menu (the same numbers and letters as the Ubuntu menu).",
 "Show-More": "The 'More' menu with the advanced jobs.",
 "New-Page": "Make one page of the guided installer (only one page shows at a time).",
 "Add-Text": "Put a block of text on a page.",
 "Show-Page": "Show page number n and set the Back/Next buttons for it.",
}
for path in sys.argv[1:]:
    lines = Path(path).read_text(encoding="utf-8").split("\n")
    out, added = [], 0
    for line in lines:
        m = re.match(r"^function ([A-Za-z-]+)", line)
        if m and m.group(1) in C and not (out and out[-1].lstrip().startswith("#")):
            out.append("# " + C[m.group(1)]); added += 1
        out.append(line)
    Path(path).write_text("\n".join(out), encoding="utf-8", newline="")
    print(path, "comments added:", added)
