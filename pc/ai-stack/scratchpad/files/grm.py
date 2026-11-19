from pathlib import Path

p = "tools/release/README.md"
s = Path(p).read_bytes().decode("utf-8")
nl = "\r\n" if "\r\n" in s else "\n"
def rep(a, b):
    global s
    a, b = a.replace("\n", nl), b.replace("\n", nl)
    assert s.count(a) == 1, a[:60]
    s = s.replace(a, b)
rep("""| `shoot-wizard.ps1` | Pictures of each page of the Windows guided installer, drawn by the real installer (nothing is installed) |
""", """| `shoot-wizard.ps1` | Pictures of each page of the Windows guided installer, drawn by the real installer (nothing is installed) |
| `mark_pictures.py` | The orange rings and notes ("click Next") on the installer pictures, always drawn on a clean copy |
| `term_shot.py` | A picture of a terminal window from real program output, with its colours and orange rings |
| `capture-menus.sh`, `make_menu_pictures.py` | The newest menu pictures (rename, switch, clean-up): the real output on a test garden, with sample names, then drawn |
| `build_release.py` | Everything above in one go, from a copy of only the files git tracks: the downloads, the PDFs, the web handbook |

## Where the data download and the Drive files came from

| Script | What it does |
|---|---|
| `export-from-server.sh` | How the garden's data was taken from the old live server: the plants database, photos, data and AI cache, in the garden's own backup format. The logins (password hashes, sessions) were left out on purpose: the new owner sets his own |
| `upload-to-drive.sh` | How the three downloads and the PDFs go to the Google Drive folder, how each file is checked there (Drive's fingerprint must match), and the SHA-256 fingerprints for `DOWNLOADS-SHA256.txt`. The Drive account, the folder's link and any keys are given when it runs; none of them are in the package |
"""),
rep("""## Testing (in `tests/`)""", """## Testing (in `tests/`)"""),
rep("""| `test-webname.sh` | The Website name swap on a throwaway copy |
""", """| `test-webname.sh` | The Website name swap on a throwaway copy |
| `test-rename-and-switch.sh` | The rename / move tool and the on / off switch (menu M, 18 and 19): a photo folder, a plant photo named in the database, a page, the redo after a restore, refusals, a big folder, the whole garden folder and back, the clean-up, the check-up, off and on |
| `test-windows-functions.ps1` | The new parts of the Windows menu on their own: the WSL path, Docker Desktop's checks, line endings, the copy that follows a rename, and finding the garden computer again after its address changed |
"""),
Path(p).write_text(s, encoding="utf-8", newline="")
print("ok")
