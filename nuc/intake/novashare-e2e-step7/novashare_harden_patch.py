#!/usr/bin/env python3
"""novashare_harden_patch.py - Novashare step 7 (2026-11-24): the drop site under one strict CSP + the audit fixes.

    python3 novashare_harden_patch.py TARGET_DIR      (TARGET_DIR = a fresh copy of ~/nuc-homelab/intake)

Copies this folder's files into TARGET_DIR. Refuses (exit 1, nothing written) if a live file it replaces is not the
2026-11-24 version it was written against (sha256 below): then the patch must be redone on the new live code.
Also writes the new Dockerfile (base pinned by digest) and removes the burned files from TARGET_DIR, so the sandbox
image is exactly what the live image will be. SWAP-PATCH.sh installs only *.py; harden_swap.sh does the rest.
"""
import hashlib
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
T = pathlib.Path(sys.argv[1])
BASE = {   # the live files this patch was written against (pulled 2026-11-24 ~09:00)
    "abyss_keyring_js.py": "d64d142fe1ed219035e9a8a208019f0e2403378e48ea1932e1e0bef51b845b18",
    "app.py": "490a1e285bc2dfc8c0edd936bb09e9615a912839e55f30caa5e457f1a421883d",
    "atomic_ui.py": "c17223afb5afe2e80d9451d25266c01f74ac30a0d63f70a517dea40b9797c571",
    "devices.py": "dde8ee64b30523e540cc9575087cb25b81f215a05a9f29602041730d8b8b3b68",
    "devices_js.py": "883e59212aee7f418d8a1200fbf946f5a2d741554f08737a2a3a86bf4e9da96a",
    "gateway.py": "929319da25e0204b235d6541b7151ec50ecd916b910575782b032ab79558f693",
    "link_probe.py": "c10538f5a46b4f24dd31e884a630c355b2a6abc5ca4e8e1d59f46216e279a21a",
    "pick_js.py": "ac5ca70297a06b28977528e36f721f6f410b01a8ca57bc12ff1c4b5f883dd937",
    "portal.py": "c207f69dec70852adf8b710270746f6f05c587949da9765a35e918e9e79243b8",
    "push_js.py": "0e2d1c916ef99bbb341cb8e0894c95c98998f90045fb9aa25e71866294960b2d",
    "receive.py": "02ba29a72b68b292dca200148886b70b89137f528ab6fc234eb2375fdf875301",
    "receive_js.py": "832502ceaf447e03e77d589c55e950754848026ab10fd75d88a907f69993146b",
    "relay.py": "bf8bc0f7417ee5780051e3c6434789a351746b062d8c972c1712c34485ce874f",
    "relay_js.py": "13903153f3df02080583967b6b565561b4f962fd541b9fc14f1c4ddfdae96c28",
    "share.py": "ac0ed4c12a2a12b7067bf52b2c3fe5fea4fbca2576f4c95484a81ae40344284c",
    "test_devices.py": "2e438766defccc04b675e509ebc116edfa7fa67bba480861bb1c7de02f9d4213",
    "themes.py": "7ad4758e8bd9b7285ce107790278ec7aa3b976b247da0d3a68b9defc755c09fe",
    "test_pick.py": "9e23b783b676f401f7db0a38cb3a6df17bdace7a45e67bccbcab57c93cee322e",
    "test_abyss_handoff.py": "3bb01f702c640036137c36211926d0b2c023ece376b416b095b94e1f2c3f05c3",
    "test_ui.py": "fa73d853a968f3d4285b4e6bf64c5c1708db1bc88acb799f570559271d77fac7",
    "Dockerfile": "61eed80cc484856cc2facf904e49658b03c872ba9543da11a877bbf5fe896740",
}
NEW = ["site_assets.py", "test_harden.py"]
# Burned (step 7): an old copy of the Drop page with inline script, and the one-time patchers that wrote the old
# inline-script pages (re-running one would put the old code back). None is imported by the app (checked: grep).
BURN = ["portal.py.bak-20261119", "atomic_patch.py", "captcha_patch.py", "gemini_patch.py", "gotenberg_web_patch.py",
        "pwa_patch.py", "share_patch.py", "gemini_probe.py", "pwa_sbx.sh"]

bad = [f for f, h in BASE.items() if hashlib.sha256((T / f).read_bytes()).hexdigest() != h]
if bad:
    print("REFUSED: these live files changed since 2026-11-24 09:00, redo the patch on them:", " ".join(bad))
    sys.exit(1)
for f in list(BASE) + NEW:
    src = HERE / ("Dockerfile.new" if f == "Dockerfile" else f)
    shutil.copyfile(src, T / f)
gone = []
for f in BURN:
    if (T / f).exists():
        (T / f).unlink()
        gone.append(f)
print(f"patched {len(BASE) + len(NEW)} files; burned in this copy: {' '.join(gone) or 'none'}")
