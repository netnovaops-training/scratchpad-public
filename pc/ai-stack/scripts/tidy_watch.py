#!/usr/bin/env python3
"""tidy_watch.py: keep All-Documents and image-files sorted as Jason drops new files in.

Jason, 2026-11-12: "ill be adding images and documents as i find them so add a watch to keep them
sorted and keep the folders more tidy ... update names", "image-files ... documented as to where and
what asset it has to do with and what tool helps with it and meta data just like a scratchpad", and
"if the file size will be much smaller convert to pdf automatically using my containers". Goals he
set: efficiency, security, privacy, then readability.

    python -I scripts/tidy_watch.py            one pass (the scheduled task runs this every 15 min)
    python -I scripts/tidy_watch.py --dry-run  say what it would do, change nothing

DOCUMENTS: a new file anywhere in All-Documents that is not yet in the sorted layout
(<Type>\\<Topic>\\<YYYY-MM-DD or undated> <name>) is handled the same way the 2026-11-12 sort did
(D:\\doc-gather\\reorg.py rules): exact copy of a file already there -> Recycle Bin as
"reviewed - <name>"; secret-shaped name (BitLocker key, Wi-Fi config, .env, .pem) -> moved to
D:\\doc-gather\\secrets-review, never synced; otherwise topic folder + date-first name, and the file's
Created/Modified times set to that original date.
WORD -> PDF: a .docx is converted by the NUC's Gotenberg container (reached through the
paperless-ngx container, no port opened) and kept as PDF only when the PDF is at most 60% of the
.docx size; the .docx then goes to the Recycle Bin as "reviewed - ...". Tested 2026-11-12: a 30 KB
.docx became a 38 KB PDF (kept as Word), a 2.1 MB one became 1.8 MB (not small enough either).
IMAGES: image-files\\ loose images -> exact duplicates to the Recycle Bin; the rest renamed
"<YYYY-MM-DD> <short description>.<ext>" into image-files\\<YYYY>\\, date from EXIF, a millisecond
timestamp in the name (Android/Gemini style), else the file time. image-files\\IMAGES.md lists every
image: date, size, pixels, camera, where the site uses it (file name found in web\\ or rust\\),
and which tool made or edits it.
Every pass ends with one line in D:\\joblog\\events.jsonl: OK, or ERROR with the step that failed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

AI = Path(r"C:\Jasons New Site\www\ai-stack")
DOCS = AI / "All-Documents"
IMAGES = AI / "image-files"
REVIEW = Path(r"D:\doc-gather\secrets-review")
JOBLOG = Path(r"D:\joblog\events.jsonl")
RECYCLE_PS = Path(r"D:\doc-gather\recycle_reviewed.ps1")
NUC = "server@192.168.1.86"
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
# Names seen in Jason's drops on 2026-11-12: github-recovery-codes.txt, stripe_backup_code.txt,
# Master_Communication_Key*.pdf, config-pfSense...xml (a firewall backup holds password hashes).
SECRET = re.compile(r"(?i)bitlocker|recovery[ _-]?(key|code)|wpa_supplicant|\.env$|\.pem$|\.key$|password|"
                    r"master[ _-]?key|backup[ _-]?codes?|communication[ _-]?key|^config-.*\.xml$|pfsense.*\.xml$")
ROBIN = re.compile(r"(?i)garden|for-robin|(^|[^a-z])juns?([^a-z]|$)")
KEEP_TOPS = {"Projects", "Budget", "Photos", "Software and Games", "Other Files"}
sys.path.insert(0, r"D:\doc-gather")


def event(step: str, ok: bool, detail: str = "") -> None:
    JOBLOG.parent.mkdir(parents=True, exist_ok=True)
    with JOBLOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "host": "pc",
                             "script": "tidy_watch", "step": step, "status": "OK" if ok else "ERROR",
                             "detail": detail[:300]}) + "\n")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def recycle(paths: list[Path], dry: bool) -> None:
    if not paths or dry:
        return
    lst = Path(r"D:\doc-gather\recycle-list-watch.txt")
    lst.write_text("\n".join(str(p) for p in paths), encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(RECYCLE_PS),
                    str(lst), r"D:\doc-gather\recycled-log-watch.csv"], capture_output=True)


def set_times(p: Path, day: str) -> None:
    t = dt.datetime.fromisoformat(day + "T12:00:00").timestamp()
    os.utime(p, (t, t))
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"(Get-Item -LiteralPath '{str(p).replace(chr(39), chr(39) * 2)}').CreationTime = "
                    f"[datetime]'{day} 12:00'"], capture_output=True)


# ---------------------------------------------------------------- documents
def to_pdf_if_smaller(p: Path, dry: bool) -> Path:
    """Word -> PDF through Gotenberg on the NUC; keep the PDF only if it is <= 60% of the size."""
    if p.suffix.lower() != ".docx" or dry:
        return p
    r = subprocess.run(["ssh", NUC, "docker exec -i paperless-ngx curl -s -f -F 'files=@-;filename=in.docx' "
                        "http://gotenberg:3000/forms/libreoffice/convert"],
                       stdin=p.open("rb"), capture_output=True, timeout=300)
    pdf = r.stdout
    if r.returncode != 0 or not pdf.startswith(b"%PDF") or len(pdf) > 0.6 * p.stat().st_size:
        return p
    out = p.with_suffix(".pdf")
    if out.exists():
        return p
    out.write_bytes(pdf)
    st = p.stat()
    os.utime(out, (st.st_atime, st.st_mtime))
    recycle([p], dry)
    return out


def sort_documents(dry: bool) -> int:
    import reorg  # D:\doc-gather\reorg.py: topic rules, clean names, original dates
    known = {}
    todo = []
    for dp, dns, fns in os.walk(DOCS):
        rel = Path(dp).relative_to(DOCS).parts
        if rel and rel[0] in KEEP_TOPS:
            dns[:] = []
            continue
        for f in fns:
            p = Path(dp, f)
            sorted_ok = len(rel) == 2 and (reorg.DATED.match(f) or f.startswith("undated ") or rel[0] == "Books")
            if sorted_ok:
                known.setdefault(p.stat().st_size, []).append(p)
            else:
                todo.append(p)
    moved = 0
    dupes = []
    for p in todo:
        try:
            moved += handle_document(p, known, dupes, dry)
        except OSError as e:
            # One locked file (Access denied: open in another program) stopped the whole first
            # run on 2026-11-12. Now it is logged and the rest are still sorted; it is retried next pass.
            event("documents", False, f"{p.name}: {type(e).__name__} {e.strerror or ''}")
            print("ERROR", p.name, type(e).__name__)
    recycle(dupes, dry)
    return moved + len(dupes)


def handle_document(p: Path, known: dict, dupes: list, dry: bool) -> int:
    import reorg
    import sort_new
    if True:
        if ROBIN.search(str(p.relative_to(DOCS))):
            return 0  # Robin's material is left exactly where it is (first dry run tried to sort it)
        if SECRET.search(p.name):
            if not dry:
                REVIEW.mkdir(parents=True, exist_ok=True)
                import shutil  # C: -> D:, so a plain rename fails (WinError 17, first run 2026-11-12)
                import stat
                os.chmod(p, stat.S_IWRITE)  # Jason's secret files were read-only: the move failed
                shutil.move(str(p), str(REVIEW / p.name))
                os.chmod(REVIEW / p.name, stat.S_IREAD)  # ...and stay read-only in secrets-review
            return 1
        same = [q for q in known.get(p.stat().st_size, []) if sha(q) == sha(p)]
        if same:
            dupes.append(p)
            return 0
        kind = sort_new.kind(str(p))  # the D:\doc-gather helpers take text paths, not Path objects
        if not kind:
            return 0  # not a document: left for Jason (photos and programs have their own folders)
        p = to_pdf_if_smaller(p, dry)
        kind = sort_new.kind(str(p))
        day = reorg.original_date(p)
        stem = reorg.clean(p.stem)
        if reorg.VAGUE.match(stem or "x"):
            stem = reorg.short(reorg.inner_title(p) or stem or "document")
        topic = reorg.topic_of(str(p.relative_to(DOCS)))
        dst = DOCS / kind / topic / f"{day or 'undated'} {reorg.short(stem, 90)}{p.suffix}"
        n = 2
        while dst.exists():
            dst = dst.with_name(f"{day or 'undated'} {reorg.short(stem, 90)} ({n}){p.suffix}")
            n += 1
        print(("would sort " if dry else "sorted ") + f"{p.name} -> {dst.relative_to(DOCS)}")
        if not dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            p.rename(dst)
            if day:
                set_times(dst, day)
            known.setdefault(dst.stat().st_size, []).append(dst)  # catches copies within this batch
        return 1


# ---------------------------------------------------------------- images
def jpeg_info(b: bytes):
    """(width, height, camera, taken YYYY-MM-DD) from a JPEG, no extra libraries."""
    w = h = None
    i = 2
    while i < len(b) - 9:
        if b[i] != 0xFF:
            i += 1
            continue
        marker, size = b[i + 1], struct.unpack(">H", b[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            break
        i += 2 + size
    m = re.search(rb"(\d{4}):(\d{2}):(\d{2}) \d{2}:\d{2}:\d{2}", b[:131072])
    cam = re.search(rb"(Apple|samsung|Google|Canon|NIKON|SONY|motorola|OnePlus)[\x00 ]([ -~]{2,30})", b[:131072])
    taken = "-".join(x.decode() for x in m.groups()) if m else None
    return w, h, (cam.group(0).decode("ascii", "replace").replace("\x00", " ").strip() if cam else ""), taken


def png_info(b: bytes):
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", b[16:24]) + ("", None)
    return None, None, "", None


def image_date(p: Path, taken):
    if taken and taken[:4] >= "1990":
        return taken
    m = re.match(r"(1[5-9]\d{11})", p.stem)  # Android / Gemini: milliseconds since 1970
    if m:
        return dt.datetime.fromtimestamp(int(m.group(1)) / 1000).date().isoformat()
    return dt.date.fromtimestamp(p.stat().st_mtime).isoformat()


def source_of(name: str) -> tuple[str, str]:
    """(what it is, which tool made or edits it), from the original file name."""
    n = name.lower()
    if n.startswith("gemini_generated"):
        return "AI picture", "made with Google Gemini"
    if re.match(r"1[5-9]\d{11}", n):
        return "phone screenshot or saved image", "Android phone (Samsung Gallery editor for ~2 / (1) copies)"
    if re.match(r"[0-9a-f]{8}-[0-9a-f]{4}-", n):
        return "downloaded or shared image", "saved from a web page, chat or app (random-ID name)"
    if re.match(r"[0-9a-f]{20,}", n):
        return "camera photo copy", "iPhone / iCloud export"
    return "image", ""


def tidy_images(dry: bool) -> int:
    if not IMAGES.is_dir():
        return 0
    done = 0
    seen = {}
    for p in sorted(IMAGES.rglob("*")):
        if p.is_file() and p.suffix.lower() in IMG_EXT:
            seen.setdefault(sha(p), []).append(p)
    dupes = [p for group in seen.values() for p in group[1:]]
    recycle(dupes, dry)
    done += len(dupes)
    rows = []
    site_files = [*(AI / "web").rglob("*.htm*"), *(AI / "rust").rglob("*.html"), *(AI / "rust").rglob("*.rs")]
    site_text = "\n".join(f.read_text("utf-8", "replace") for f in site_files if f.stat().st_size < 2_000_000)
    for group in seen.values():
        p = group[0]
        if p in dupes or not p.exists():
            continue
        b = p.read_bytes()
        w, h, cam, taken = jpeg_info(b) if p.suffix.lower() in (".jpg", ".jpeg") else png_info(b)
        day = image_date(p, taken)
        what, tool = source_of(p.name)
        sorted_already = p.parent.name == day[:4] and p.name.startswith(day)
        if not sorted_already:
            dst = IMAGES / day[:4] / f"{day} {what}{p.suffix.lower()}"
            n = 2
            while dst.exists():
                dst = dst.with_name(f"{day} {what} ({n}){p.suffix.lower()}")
                n += 1
            if not dry:
                dst.parent.mkdir(parents=True, exist_ok=True)
                original = p.name
                p.rename(dst)
                set_times(dst, day)
                (IMAGES / ".original-names.tsv").open("a", encoding="utf-8").write(f"{dst.relative_to(IMAGES)}\t{original}\n")
                p = dst
            done += 1
        used = "on the site" if p.name in site_text else ""
        rows.append((str(p.relative_to(IMAGES)).replace("\\", "/"), day, f"{p.stat().st_size // 1024} KB",
                     f"{w}x{h}" if w else "", cam, what, tool, used))
    if not dry:
        lines = ["# image-files: every image, where it came from, and what uses it",
                 "", "Kept by `scripts/tidy_watch.py` (runs every 15 minutes). Drop new images straight into "
                 "`image-files`; they are de-duplicated, dated from their own metadata, renamed and filed by "
                 "year. Original names: `.original-names.tsv`.", "",
                 "| File | Date | Size | Pixels | Camera | What it is | Tool | Used |", "|---|---|---|---|---|---|---|---|"]
        lines += ["| `%s` | %s | %s | %s | %s | %s | %s | %s |" % r for r in sorted(rows)]
        (IMAGES / "IMAGES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return done


def main() -> int:
    dry = "--dry-run" in sys.argv
    d = 0
    try:
        d = sort_documents(dry)
        event("documents", True, f"{d} handled")
    except Exception as e:  # noqa: BLE001  images still run when documents fail
        event("documents", False, f"{type(e).__name__}: {e}")
        print("ERROR documents:", e)
    try:
        i = tidy_images(dry)
        event("images", True, f"{i} handled")
    except Exception as e:  # noqa: BLE001
        event("images", False, f"{type(e).__name__}: {e}")
        print("ERROR images:", e)
        return 1
    print(f"{'DRY ' if dry else ''}OK documents {d}, images {i}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
