"""pc_env_to_pass.py - move the last plain secrets in the PC's ai-stack .env into Proton Pass (2026-11-24).

    py -3.14 -I D:\\scratchpad\\tools\\pc_env_to_pass.py

SECRETS-TO-PASS-PLAN.md step 2 (PC). Of 91 plain .env entries, a name/shape scan found these to move:
INTAKE_KEY and PORTAINER_AGENT_SECRET (real secrets), TWILIO_ACCOUNT_SID (an account id; moved too). The other
88 are settings. For each: the value is read into a variable (never printed), saved as a Login item in vault
"API keys" through stdin (`item create login --from-template -`), read back and compared by fingerprint, and only
then the .env line becomes `NAME=pass://API keys/<title>/password` (ai-compose.cmd already runs
`pass-cli run --env-file .env`). A mismatch leaves the line unchanged. No backup copy of the plain file is made:
Pass holds the value before the line is changed. Re-running skips names already holding a pass:// reference.
"""
import hashlib
import json
import pathlib
import re
import subprocess
import sys

ENV = pathlib.Path(r"C:\Jasons New Site\www\ai-stack\.env")
VAULT = "API keys"
NAMES = ["INTAKE_KEY", "PORTAINER_AGENT_SECRET", "TWILIO_ACCOUNT_SID"]
DATE = "2026-11-24"


def fp(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:8]


def pas(*args, inp=None):
    return subprocess.run(["pass-cli", *args], input=inp, capture_output=True, text=True, encoding="utf-8")


text = ENV.read_text(encoding="utf-8")
for name in NAMES:
    m = re.search(rf"^{name}=(.*)$", text, re.M)
    if not m:
        print(f"{name}: not in .env, skipped")
        continue
    value = m.group(1).strip().strip('"').strip("'").replace("\r", "")
    if value.startswith("pass://"):
        print(f"{name}: already a reference, skipped")
        continue
    title = f"PC ai-stack {name} ({DATE})"
    if pas("item", "view", "--vault-name", VAULT, "--item-title", title, "--field", "username").returncode != 0:
        tpl = {"title": title, "username": name, "email": None, "password": value, "totp_uri": None, "urls": [],
               "extra_fields": []}
        r = pas("item", "create", "login", "--vault-name", VAULT, "--from-template", "-", inp=json.dumps(tpl))
        if r.returncode != 0:
            print(f"{name}: Pass create failed (rc {r.returncode}); line unchanged")
            continue
    back = pas("item", "view", "--vault-name", VAULT, "--item-title", title, "--field", "password").stdout.strip()
    if fp(back) != fp(value):
        print(f"{name}: read-back DIFFERS (len {len(back)} vs {len(value)}); line unchanged")
        continue
    text = text[:m.start()] + f"{name}=pass://{VAULT}/{title}/password" + text[m.end():]
    print(f"{name}: in Pass (len {len(value)}, fp {fp(value)}), .env now references it")
ENV.write_text(text, encoding="utf-8", newline="")
sys.exit(0)
