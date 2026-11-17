import sys

def load(p):
    d = {}
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        h, f = line.split(None, 1)
        d[f.lstrip("*")] = h
    return d

live, pkg = load(sys.argv[1]), load(sys.argv[2])
missing = [f for f in live if f not in pkg]
diff = [f for f in live if f in pkg and pkg[f] != live[f]]
print("missing in package:", len(missing))
print("\n".join(missing))
print("different:", len(diff))
print("\n".join(diff))
