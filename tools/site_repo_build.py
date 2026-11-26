#!/usr/bin/env python3
"""site_repo_build.py - build the one "homelab-site" repo from every machine's code (T0913, 2026-11-19).

    PC:   py -3 -X utf8 site_repo_build.py pc-pack OUT.tar.gz
              packs the git-TRACKED files of the PC site (ai-stack) and D:/scratchpad, text only, < 1 MB.
    NUC:  python3 site_repo_build.py build DEST NUC_SNAP.tar.gz PI_SNAP.tar.gz PC_PACK.tar.gz
              writes DEST/machines/{nuc,pi,pc}/... from the three archives, through the same filter.
              DEST must not exist yet (it is never cleaned or overwritten: no deletes anywhere).

Why: Jason 2026-11-19 wants "a streamlined copy of the entire website including the files in root like
cron jobs", kept on Gitea ONLY (never GitHub), with copies on D:, the NAS and the USB drives (T0914).
The NUC and Pi parts come from machine_snapshot.sh archives (home code + crontab.txt + system/), so the
repo is a picture of a known moment that machine_restore.sh can also put back.

The filter (one function, KEEP): text files only (no NUL byte in the first 8 KB), under 1 MB, and never a
file whose name looks like a secret or a leftover: .env*, keys, certificates, keystores, credential stores,
kopia/rclone configs, *.bak / *.before-* / *.stale* copies, logs and .jsonl data. Secrets that sit INSIDE
an allowed file are caught after the build by gitleaks + secret_scan.py (T0914); the build does not commit.
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
import tarfile
from pathlib import Path

MAX = 1_000_000
SECRETISH = re.compile(
    r"(?i)(^|/)(\.env(\..*)?|.*\.env|\.runner|hosts\.yml|.*\.(pem|key|p12|pfx|jks|keystore|kdbx|crt|cer|der|token|secret|log|jsonl|sqlite3?|db)"
    r"|id_(rsa|ed25519|ecdsa)(\.pub)?|\.git-credentials|\.[a-z]*_history|\.lesshst|\.viminfo|\.wget-hsts|\.netrc|\.pgpass|credentials(\..*)?|rclone\.conf"
    r"|.*\.bak(-.*)?|.*\.bak\..*|.*\.before-.*|.*\.stale.*|.*\.orig)$")
SECRET_DIRS = re.compile(r"(?i)(^|/)(\.ssh|\.secrets|secrets|\.config/gh|runner-data|fixwork|\.gnupg|\.config/kopia[^/]*|\.config/rclone|\.docker|"
                         r"\.git|node_modules|__pycache__|\.venv|venv|target)(/|$)")


CONFIG_EXT = (".yml", ".yaml", ".json", ".toml", ".conf", ".ini", ".cfg", ".properties")
# key: value / "key":"value" where the key names a secret and the value is 12+ token characters.
# Found by gitleaks on the first builds 2026-11-19: Tandoor OIDC client secret inside docker-compose.web.yml
# (json in yaml), searxng secret_key, homepage widget key. Placeholders like ${VAR} never match ([^$...]).
SECRET_VALUE = re.compile(
    # [\w-]* prefix: also "bottoken" (Grafana Telegram contact point held a real bot token, found by
    # secret_scan.py 2026-11-19 after gitleaks missed it), "db_password", "x-api-key".
    r"""(?ix)(["']?[\w-]*(?:secret|key|password|passwd|token)["']?
        \s*[:=]\s*["']?)([A-Za-z0-9_\-+/=.:]{12,})""")


def redact(name: str, data: bytes) -> bytes:
    """Config files: replace secret-named values with <redacted> (names stay so the shape is readable)."""
    if not name.lower().endswith(CONFIG_EXT):
        return data
    text = data.decode("utf-8", "replace")
    return SECRET_VALUE.sub(lambda m: m.group(1) + "<redacted>", text).encode("utf-8")


# Top-level home folders that are copies, not code (rebuild 2026-11-24, autopilot T0913): the 11-24 NUC snapshot
# grew the tree from 6,264 to 27,946 files because of sandbox/ (11,143 files: sbx-* test copies of services),
# homelab-site/ (6,359: a checkout of THIS repo, so the repo copied itself) and restore-2026-11-19_1832/
# (2,850: the machine_restore.sh test). The 11-19 tree had none of them. Anchored at ^ so only the home top
# level matches; PC names start with ai-stack/ or scratchpad/ and never match.
# Second pass the same day: data and personal letters had appeared at the home top level since 11-19 (abyss-data
# album grants, email-photos captions, alerts state, career-plan = its own Gitea repo, apology-*.html and
# weekly-*-preview.html letters, review/t1014 *.json notes). "Code only, no data" (T0913 Do 3), so: those
# folders, and any .html/.json/.prom file sitting directly in a home folder (no slash in the name).
NOT_CODE = re.compile(r"^(sandbox|homelab-site|restore-[0-9][^/]*|abyss-data|email-photos|alerts|career-plan)/"
                      r"|^[^/]+\.(html|json|prom)$")


def keep(name: str, data: bytes) -> bool:
    """True when this file belongs in the repo: small text, not secret-shaped, not a leftover copy."""
    if len(data) >= MAX or b"\0" in data[:8192]:
        return False
    return not (SECRETISH.search(name) or SECRET_DIRS.search(name) or NOT_CODE.search(name))


def pc_pack(out: str) -> None:
    """Tar the tracked text files of the PC site and the scratchpad (paths are literal, see ROOTS)."""
    roots = {"ai-stack": Path(r"C:\Jasons New Site\www\ai-stack"), "scratchpad": Path(r"D:\scratchpad")}
    n = 0
    with tarfile.open(out, "w:gz") as tar:
        for label, root in roots.items():
            files = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True,
                                   check=True).stdout.decode("utf-8", "replace").split("\0")
            for rel in filter(None, files):
                p = root / rel
                if not p.is_file() or p.stat().st_size >= MAX:
                    continue
                data = p.read_bytes()
                if keep(rel, data):
                    tar.add(str(p), arcname=f"{label}/{rel}")
                    n += 1
    print(f"packed {n} files -> {out}")


def build(dest: str, nuc: str, pi: str, pc: str) -> None:
    """Extract the three archives into DEST/machines/<machine>/ through keep(); DEST must be new."""
    root = Path(dest)
    root.mkdir(parents=True)  # FileExistsError on purpose if it is already there
    counts = {}
    for machine, archive in (("nuc", nuc), ("pi", pi), ("pc", pc)):
        kept = skipped = 0
        with tarfile.open(archive, "r:gz") as tar:
            for m in tar:
                if not m.isfile():
                    continue
                # removeprefix, NOT lstrip("./"): lstrip also ate the dot of ".git-credentials" and
                # ".bash_history", so the secret filter missed them (first build 2026-11-19, caught by gitleaks).
                name = m.name.removeprefix("./")
                if name == "files.txt":
                    continue
                data = tar.extractfile(m).read()
                if not keep(name, data):
                    skipped += 1
                    continue
                # map snapshot layout: crontab.txt and system/ stay top-level, home files go under home/
                sub = name if machine == "pc" or name == "crontab.txt" or name.startswith("system/") \
                    else f"home/{name}"
                target = (root / "machines" / machine / sub).resolve()
                if root.resolve() not in target.parents:  # no ../ escapes
                    skipped += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(redact(name, data))
                kept += 1
        counts[machine] = (kept, skipped)
    for machine, (k, s) in counts.items():
        print(f"{machine}: kept {k}, left out {s}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "pc-pack":
        pc_pack(sys.argv[2])
    elif len(sys.argv) == 6 and sys.argv[1] == "build":
        build(*sys.argv[2:])
    else:
        sys.exit(__doc__)
