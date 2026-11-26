"""test_torlink.py - proof for patch_torlink.py: the Tor Browser link shows for the admin (jason) only.

    docker run --rm --entrypoint python IMAGE test_torlink.py
"""
import os
import pathlib
import sys

for k, v in {"INTAKE_KEY": "k-test", "INTAKE_TOKEN": "t-test", "LLM_BASE": "http://x", "AUTH_LEVELS_URL": "http://x",
             "GOTENBERG_URL": "http://x"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import portal  # noqa: E402

admin = portal.page("a@example.invalid", "jason")
other = portal.page("b@example.invalid", "casey")
ok1 = 'href="https://tor.home.example.net/">Tor Browser</a>' in admin
ok2 = "tor.example" not in other
print(("PASS" if ok1 else "FAIL") + " Tor Browser link for the admin")
print(("PASS" if ok2 else "FAIL") + " no Tor link for others")
print("ALL PASS" if ok1 and ok2 else "FAILED")
sys.exit(0 if ok1 and ok2 else 1)
