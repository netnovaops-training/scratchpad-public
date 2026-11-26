"""T0917: list code files that differ between two images' work dirs (old running image vs candidate).
Usage: img_diff.py <old_image> <new_image> [path=/app]. Prints paths + counts, never file contents.
CR is stripped before hashing (tested 11-24: Windows CRLF copies showed as false drift).
"""
import hashlib, io, subprocess, sys, tarfile
EXTS = (".py", ".js", ".mjs", ".html", ".css", ".sh", ".txt", ".json", ".toml")

def files(image, path):
    cid = subprocess.run(["docker", "create", image], capture_output=True, text=True).stdout.strip()
    try:
        tar = subprocess.run(["docker", "cp", f"{cid}:{path}", "-"], capture_output=True).stdout
    finally:
        subprocess.run(["docker", "rm", cid], capture_output=True)
    out = {}
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        for m in t.getmembers():
            if m.isfile() and m.name.endswith(EXTS) and "__pycache__" not in m.name:
                out[m.name.split("/", 1)[-1]] = hashlib.sha1(t.extractfile(m).read().replace(b"\r\n", b"\n")).hexdigest()
    return out

old, new = files(sys.argv[1], sys.argv[3] if len(sys.argv) > 3 else "/app"), files(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "/app")
print("same", sum(1 for k in old if new.get(k) == old[k]))
print("changed", sorted(k for k in old if k in new and new[k] != old[k]))
print("only_old", sorted(k for k in old if k not in new))
print("only_new", sorted(k for k in new if k not in old))
