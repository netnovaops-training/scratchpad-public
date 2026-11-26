"""env_copies_to_pass.py - clean the leftover plain-text .env copies on the PC, with a rollback copy in Pass (2026-11-24).

    pass-cli run --env-file "C:\\Jasons New Site\\www\\ai-stack\\.env" -- py -3.14 -I D:\\scratchpad\\tools\\env_copies_to_pass.py          (dry run)
    pass-cli run --env-file "C:\\Jasons New Site\\www\\ai-stack\\.env" -- py -3.14 -I D:\\scratchpad\\tools\\env_copies_to_pass.py --apply

Jason, 11-24: "Make them correct, use a backup so they can be rolled back". After the live .env files moved to
Proton Pass (pc_env_to_pass.py, pi_env_to_pass.py), old copies still held plain secrets: the PC's ai-stack\\pi\\.env
(source of the Pi's .env), D:\\ai-stack-backup-2026-11-12\\ and D:\\ai-stack-archive\\outdated\\.
For each file:
  1. Rollback copy FIRST: the whole file goes into a Proton Pass secure note (vault "Notes", title
     "env-backup 2026-11-24 <path>") through stdin, and is read back and compared by fingerprint. If that fails,
     the file is not touched.
  2. Each secret line (secret-looking name, a long random value, or a password inside a URL) becomes:
       - the same pass:// reference the live file uses, when the old value equals today's live value
         (fingerprint compare; the live values come from `pass-cli run`, held in memory only), or
       - an empty value with a comment naming the Pass note, when the value is old (rotated) or unknown.
Never prints a value: names, lengths and 8-char fingerprints only. Rollback: copy the note's text back to the path.
"""
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

APPLY = "--apply" in sys.argv
DATE = "2026-11-24"
NOTE_VAULT = "Notes"
LIVE_ENV = pathlib.Path(r"C:\Jasons New Site\www\ai-stack\.env")
FILES = [pathlib.Path(p) for p in [
    r"C:\Jasons New Site\www\ai-stack\pi\.env",
    r"D:\ai-stack-backup-2026-11-12\.env",
    r"D:\ai-stack-backup-2026-11-12\logging\.env",
    r"D:\ai-stack-backup-2026-11-12\moneymatter\.env",
    r"D:\ai-stack-backup-2026-11-12\pi\.env",
    r"D:\ai-stack-backup-2026-11-12\wazuh\wazuh-docker\.env",
    r"D:\ai-stack-backup-2026-11-12\wazuh-docker\.env",
    r"D:\ai-stack-archive\outdated\logging\.env",
    r"D:\ai-stack-archive\outdated\moneymatter\.env",
    r"D:\ai-stack-archive\outdated\wazuh\wazuh-docker\.env",
    r"D:\ai-stack-archive\outdated\wazuh-docker\.env",
]]
# The Pi's two secrets live in vault "NUC services" (pi_env_to_pass.py), not in the PC .env.
PI_REFS = {n: f"pass://NUC services/Pi stack {n} ({DATE})/password" for n in ("N8N_ENCRYPTION_KEY", "FIREFOX_PASSWORD")}
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|_PASS$|PWD|SALT|PRIVATE|CRED|COOKIE|HMAC|SIGNING|DSN", re.I)
NOT_SECRET = re.compile(r"EMAIL|DAYS|HOURS|MINUTES|_MODEL$|_USER$|_URL$|_PATH$|_PORT$|_HOST$", re.I)
# [ \t] not \s: \s matched newlines, so an empty "NAME=" took the NEXT line as its value (found 11-24).
LINE = re.compile(r"^([ \t]*)([A-Za-z_][A-Za-z0-9_]*)([ \t]*=[ \t]*)([^\r\n]*?)(\r?)$", re.M)


def fp(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:8]


def pas(*args, inp=None):
    return subprocess.run(["pass-cli", *args], input=inp, capture_output=True, text=True, encoding="utf-8")


def is_secret(name: str, value: str) -> bool:
    if not value or value.startswith("pass://") or value.startswith("${"):
        return False
    if re.match(r"\w+://[^/@\s]+:[^/@\s]+@", value):
        return True
    if NOT_SECRET.search(name):
        return False
    return bool(SECRET_NAME.search(name)) or (len(value) >= 20 and re.fullmatch(r"[A-Za-z0-9_\-+/=]+", value) is not None)


# Live references and live value fingerprints (values only in this process's environment, from pass-cli run).
live_refs = {m.group(2): m.group(4).strip().strip('"') for m in LINE.finditer(LIVE_ENV.read_text(encoding="utf-8"))
             if m.group(4).strip().strip('"').startswith("pass://")}
live_fp = {n: fp(os.environ[n]) for n in live_refs if os.environ.get(n)}
pi_fp = {}
for n in PI_REFS:
    r = pas("item", "view", "--vault-name", "NUC services", "--item-title", f"Pi stack {n} ({DATE})", "--field", "password")
    if r.returncode == 0 and r.stdout.strip():
        pi_fp[n] = fp(r.stdout.strip())
print(f"live references: {len(live_refs)} (resolved {len(live_fp)}), Pi items resolved: {len(pi_fp)}")
if len(live_fp) < len(live_refs) // 2:
    sys.exit("pass-cli run did not resolve the live .env; run this under pass-cli run (see the docstring)")

for path in FILES:
    if not path.is_file():
        print(f"{path}: missing, skipped")
        continue
    raw = path.read_bytes().decode("utf-8", errors="strict")
    title = f"env-backup {DATE} {path}"
    plan = []

    def fix(m):
        ind, name, eq, val, cr = m.groups()
        v = val.strip().strip('"').strip("'")
        if not is_secret(name, v):
            return m.group(0)
        refs = {**live_refs, **PI_REFS}
        known = {**live_fp, **pi_fp}
        if name in refs and known.get(name) == fp(v):
            plan.append(f"{name}: same as live, now {refs[name][:7]}... reference")
            return f"{ind}{name}{eq}{refs[name]}{cr}"
        plan.append(f"{name}: old/unknown value (len {len(v)}, fp {fp(v)}), emptied")
        return f"{ind}# {name}: old value removed {DATE}; the original file is in Pass note \"{title}\"{cr}\n{ind}{name}{eq}{cr}"

    new = LINE.sub(fix, raw)
    print(f"{path}: {len(plan)} secret line(s)")
    for p in plan:
        print("   ", p)
    if not plan or not APPLY:
        continue
    if pas("item", "view", "--vault-name", NOTE_VAULT, "--item-title", title, "--field", "note").returncode != 0:
        r = pas("item", "create", "note", "--vault-name", NOTE_VAULT, "--from-template", "-",
                inp=json.dumps({"title": title, "note": raw}))
        if r.returncode != 0:
            print(f"    backup note FAILED (rc {r.returncode}); file not changed")
            continue
    back = pas("item", "view", "--vault-name", NOTE_VAULT, "--item-title", title, "--field", "note").stdout
    # Pass stores notes with LF line endings; a CRLF file reads back shorter by one byte per line (seen
    # 11-24 on moneymatter\.env: 7844 vs 8040). Compare with CR removed; a rollback writes LF, which .env accepts.
    if fp(back.replace("\r", "").rstrip("\n")) != fp(raw.replace("\r", "").rstrip("\n")):
        print(f"    backup note read-back DIFFERS (len {len(back)} vs {len(raw)}); file not changed")
        continue
    path.write_bytes(new.encode("utf-8"))
    print(f"    backed up to Pass note (fp {fp(raw.rstrip(chr(10)))}) and file rewritten")
