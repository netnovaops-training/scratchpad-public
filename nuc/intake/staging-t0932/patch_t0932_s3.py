#!/usr/bin/env python3
"""patch_t0932_s3.py - T0932 step 3 (2026-11-22, autopilot): filing across disks copies, CHECKS sha256, then
removes the staging copy. Edits <copy>/app.py place(); never the live one.

    python3 patch_t0932_s3.py <intake folder copy>

Why: place() did shutil.move(tmp, part). From the staging disk to a NAS share that is a copy + delete with no
check that the bytes arrived, and a copy that failed mid-way left a half .part in the library folder (11-21
audit point 1). After step 1 the staging is on the USB SSD, so EVERY library (NAS shares, ~/documents, ~/manga)
is on another disk and every filing goes through this path. The exact-match anchor must be found once, or the
patch stops and changes nothing.
"""
import pathlib
import sys

f = pathlib.Path(sys.argv[1]) / "app.py"
s = f.read_text(encoding="utf-8")

OLD = ('    part = target.with_suffix(target.suffix + ".part")\n'
       '    shutil.move(str(tmp), str(part))\n'
       '    part.rename(target)\n'
       '    return {"status": "filed", "path": str(target)}\n')
NEW = ('    part = target.with_suffix(target.suffix + ".part")\n'
       '    # T0932 step 3 (2026-11-22): was shutil.move(tmp, part). Across disks (staging on the USB SSD -> a NAS\n'
       '    # share) that is copy + delete with no check, and a failed copy left a half .part in the library. Now:\n'
       '    # same disk = one rename (atomic, nothing copied); other disk = copy, fsync, re-read the .part and compare\n'
       '    # its sha256 with `digest` (computed from the staged file before the virus check), and only then remove the\n'
       '    # staging copy. Any failure removes OUR half .part, keeps `tmp` where it was and raises, so the caller\'s\n'
       '    # keep-it rule (resumable: FAILED with a WHY.txt) still has the bytes. Tested: test_t0932_s3.py.\n'
       '    try:\n'
       '        same_disk = tmp.stat().st_dev == dest_dir.stat().st_dev\n'
       '    except OSError:\n'
       '        same_disk = False\n'
       '    if same_disk:\n'
       '        os.replace(tmp, part)\n'
       '    else:\n'
       '        try:\n'
       '            _copy_checked(tmp, part, digest)\n'
       '        except BaseException:\n'
       '            part.unlink(missing_ok=True)\n'
       '            raise\n'
       '        tmp.unlink()\n'
       '    part.rename(target)\n'
       '    return {"status": "filed", "path": str(target)}\n'
       '\n'
       '\n'
       'def _copy_checked(src: pathlib.Path, dst: pathlib.Path, digest: str) -> None:\n'
       '    """Copy `src` to `dst` (a .part on another disk), flush it to that disk, and check its sha256 == `digest`.\n'
       '\n'
       '    T0932 step 3. Raises OSError(EIO) on a mismatch; the caller removes `dst`. The re-read can be served from\n'
       '    this machine\'s page cache for a NAS share, so it proves what the NUC wrote, not every SMB/NFS hop; the\n'
       '    fsync before it at least makes the NAS confirm the write (a full or dropped share fails HERE, not later).\n'
       '    """\n'
       '    shutil.copyfile(src, dst)\n'
       '    with dst.open("rb+") as fh:\n'
       '        os.fsync(fh.fileno())\n'
       '    got = sha256(dst)\n'
       '    if got != digest:\n'
       '        say(f"  copy check FAILED for {dst.name}: {got[:12]} != {digest[:12]}")\n'
       '        raise OSError(errno.EIO, "copy check failed: the filed copy does not match the upload")\n')

if s.count(OLD) != 1:
    sys.exit(f"anchor found {s.count(OLD)} times, nothing changed")
s = s.replace(OLD, NEW)
if "\nimport errno\n" not in s:
    if s.count("\nimport hashlib\n") != 1:
        sys.exit("no single 'import hashlib' line to put 'import errno' next to; nothing changed")
    s = s.replace("\nimport hashlib\n", "\nimport errno\nimport hashlib\n")
f.write_text(s, encoding="utf-8")
print("patched", f)
