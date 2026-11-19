#!/usr/bin/env python3
"""verify_pi.py -- check SuperNova's pi.snpi yourself. Nothing here trusts SuperNova.

pi.snpi holds no digits: only the equation (recipe), each run's size and Merkle root, a signature and a
timestamp. This script follows the equation, regenerates the digits on YOUR computer, and checks that
they land on every stored root.

  pip install gmpy2 cryptography
  python3 verify_pi.py pi.snpi                 signature only (seconds)
  python3 verify_pi.py pi.snpi --compute       also regenerate the digits and check every root
                                               (100 million decimals: a few minutes, ~1 GB RAM)
  python3 verify_pi.py pi.snpi --compute --save digits.txt    keep the regenerated digits if you want them
Timestamp: pip install opentimestamps-client; save /api/certificate/ots and run  ots verify  on it.
"""
import hashlib
import json
import struct
import sys
import time
from pathlib import Path


def sha(b):
    return hashlib.sha256(b).digest()


def root_of(d, n, block=1000):
    lv = [sha(f"{i + 1}:".encode() + d[i:min(i + block, n)].encode()) for i in range(0, n, block)]
    while len(lv) > 1:
        if len(lv) % 2:
            lv = lv + [lv[-1]]
        lv = [sha(lv[i] + lv[i + 1]) for i in range(0, len(lv), 2)]
    return lv[0].hex()


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    raw = Path(sys.argv[1]).read_bytes()
    magic, ver, count, rl, _ol = struct.unpack_from("<4sHHII", raw, 0)
    if magic != b"SNPI" or ver != 2:
        raise SystemExit("not a version-2 pi.snpi file")
    p = 16
    recipe = raw[p:p + rl]; p += rl
    runs = [(struct.unpack_from("<Q", raw, p + 40 * i)[0], raw[p + 40 * i + 8:p + 40 * i + 40].hex()) for i in range(count)]
    p += 40 * count
    sig = raw[p:p + 64]
    eq = json.loads(recipe)
    msg = recipe + b"".join(struct.pack("<Q", n) + bytes.fromhex(r) for n, r in runs)
    bad = 0

    def report(name, ok, detail=""):
        nonlocal bad
        bad += ok is False
        print(f"[{'PASS' if ok is True else 'SKIP' if ok is None else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

    print(f"pi.snpi: {len(raw):,} bytes; {count} run(s): " + ", ".join(f"{n:,}" for n, _ in runs))
    print(f"ID {hashlib.sha256(msg).hexdigest()}")
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        try:
            ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(eq["public_key_ed25519"])).verify(sig, msg)
            report("Ed25519 signature over the equation and every run", True)
        except Exception:  # noqa: BLE001  a verifier: any error in the check is a failed check
            report("Ed25519 signature over the equation and every run", False)
    except ImportError:
        report("signature", None, "pip install cryptography")
    if "--compute" in sys.argv:
        import gmpy2
        n = runs[-1][0]
        print(f"following the equation: pi to {n:,} decimals with MPFR ...", flush=True)
        t = time.time()
        gmpy2.get_context().precision = int((n + 40) * 3.3219280948873626)
        d = gmpy2.digits(gmpy2.const_pi(), 10, n + 21)[0][1:n + 1]
        print(f"      done in {time.time() - t:.1f}s")
        for rn, r in runs:
            report(f"run {rn:,}: your digits land on the stored root", root_of(d, rn) == r, r[:16] + "…")
        if "--save" in sys.argv:
            out = sys.argv[sys.argv.index("--save") + 1]
            Path(out).write_text(d)
            print(f"      digits saved to {out}")
    print("ALL CHECKS PASSED" if bad == 0 else f"{bad} CHECK(S) FAILED")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
