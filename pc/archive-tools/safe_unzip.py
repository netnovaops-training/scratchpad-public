"""safe_unzip.py ZIP OUTDIR: extract a zip only if no entry escapes OUTDIR (absolute or ../ names).
Prints entry count and the top-level names. Written 2026-11-12 for the ai-stack tidy-up."""
import os
import sys
import zipfile

z = zipfile.ZipFile(sys.argv[1])
out = os.path.abspath(sys.argv[2])
bad = [i.filename for i in z.infolist()
       if not os.path.abspath(os.path.join(out, i.filename)).startswith(out + os.sep)
       and os.path.abspath(os.path.join(out, i.filename)) != out]
print(f"entries {len(z.infolist())}, unsafe names {len(bad)}")
if not bad:
    z.extractall(out)
    tops = sorted({i.filename.replace("\\", "/").split("/")[0] for i in z.infolist()})
    print("top:", ", ".join(tops[:15]))
