"""T0917 step 2 (PC): does each running ai-stack/* container run the site-repo code?

Compares git blob ids from the site repo tree (homelab-site 0da9613, machines/pc/ai-stack)
with the code files inside each running container that compose builds from local source.
Reads compose YAML with PyYAML (no env interpolation, so no secrets are loaded or printed).
Prints counts and file paths only, never file contents.
"""
import hashlib, io, json, subprocess, sys, tarfile, os
import yaml

ROOT = r"C:\Jasons New Site\www\ai-stack"
EXTS = (".py", ".js", ".mjs", ".html", ".css", ".rs", ".sh", ".php", ".toml")
manifest_file = sys.argv[1]  # lines: "<mode> blob <sha>\t<path>"

tree = {}
for line in open(manifest_file, encoding="utf-8"):
    meta, _, path = line.rstrip("\n").partition("\t")
    parts = meta.split()
    if len(parts) == 3 and parts[1] == "blob":
        tree[path[len("machines/pc/ai-stack/"):]] = parts[2]

services = {}
for f in ("docker-compose.yml", "docker-compose.web.yml"):
    try:
        d = yaml.safe_load(open(os.path.join(ROOT, f), encoding="utf-8")) or {}
    except Exception as e:  # noqa
        print("yaml error", f, type(e).__name__); continue
    for name, svc in (d.get("services") or {}).items():
        b = svc.get("build")
        if b is None: continue
        ctx = b if isinstance(b, str) else b.get("context", ".")
        services[name] = (svc.get("container_name", name), ctx.replace("\\", "/").lstrip("./").rstrip("/"))

running = set(subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split())

def blob(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

report = []
for name, (cname, ctx) in sorted(services.items()):
    if cname not in running:
        report.append({"svc": name, "status": "not running"}); continue
    wd = subprocess.run(["docker", "inspect", "-f", "{{.Config.WorkingDir}}", cname], capture_output=True, text=True).stdout.strip() or "/app"
    p = subprocess.run(["docker", "cp", f"{cname}:{wd}", "-"], capture_output=True)
    if p.returncode:
        report.append({"svc": name, "status": "cp failed", "wd": wd}); continue
    img = {}
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as t:
        for m in t.getmembers():
            if m.isfile() and m.name.endswith(EXTS) and "/node_modules/" not in m.name and "__pycache__" not in m.name:
                rel = m.name.split("/", 1)[1] if "/" in m.name else m.name
                # git stores LF; Windows builds copy CRLF files. Tested 11-24: collage/app.py
                # differed only by CR (same 14188 bytes, 0 diff lines), so strip CR before hashing.
                img[rel] = blob(t.extractfile(m).read().replace(b"\r\n", b"\n"))
    # Match each image file to tree paths ending in the same relative path (Dockerfiles
    # COPY subfolders to /app, so the compose context alone does not give the prefix).
    same, differ, only_img, where = [], [], [], set()
    for rel, h in img.items():
        cands = [k for k in tree if k == rel or k.endswith("/" + rel)]
        if not cands:
            only_img.append(rel); continue
        hit = [k for k in cands if tree[k] == h]
        if hit:
            same.append(rel); where.add(hit[0][: len(hit[0]) - len(rel)].rstrip("/"))
        else:
            differ.append(rel + " (tree: " + cands[0] + ")")
    report.append({"svc": name, "ctx": ctx, "wd": wd, "same": len(same), "differ": differ[:10], "n_differ": len(differ),
                   "only_image": len(only_img), "only_image_eg": only_img[:5], "only_tree": sorted(where)[:3]})
print(json.dumps(report, indent=1))
