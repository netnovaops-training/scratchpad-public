"""snpi.py -- pi.snpi version 2: the stored result is an EQUATION plus its answers, nothing else
(Jason, 2026-11-09: "the input is the equation and the output is the result ... each input takes the
last result ... so the output stays extremely small").

  header       16 bytes: b"SNPI", u16 version=2, u16 runs, u32 recipe_len, u32 timestamp_len
  recipe       canonical JSON, written ONCE for all runs: the formula that produces the digits, the
               software, how a new run is double-checked, the Merkle rules, SuperNova's public key
  runs         40 bytes each, in order: u64 decimals + the 32-byte Merkle root of decimals 1..N
               (each run extends the one before it; nothing else per run)
  signature    64 bytes, Ed25519 over ID, where ID = SHA-256(recipe || all run records)
  timestamp    one OpenTimestamps proof of ID (dates the whole chain; Bitcoin)

No digits are stored. They are regenerated from the recipe when needed, and a regeneration is only
trusted when it lands on the stored root. Three runs of pi: well under 1 KB plus the timestamp.
"""
import hashlib
import json
import struct
from pathlib import Path

MAGIC, VERSION = b"SNPI", 2
HFMT = "<4sHHII"
HLEN = struct.calcsize(HFMT)          # 16


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def signed_message(recipe: bytes, runs) -> bytes:
    return recipe + b"".join(struct.pack("<Q", n) + bytes.fromhex(r) for n, r in runs)


def file_id(recipe: bytes, runs) -> str:
    return hashlib.sha256(signed_message(recipe, runs)).hexdigest()


def write(path: Path, recipe: bytes, runs, sig: bytes, ots: bytes = b""):
    body = (struct.pack(HFMT, MAGIC, VERSION, len(runs), len(recipe), len(ots)) + recipe
            + b"".join(struct.pack("<Q", n) + bytes.fromhex(r) for n, r in runs) + sig + ots)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)


def read(path: Path):
    raw = path.read_bytes()
    magic, ver, count, rl, ol = struct.unpack_from(HFMT, raw, 0)
    if magic != MAGIC or ver != VERSION:
        raise ValueError("not a version-2 pi.snpi file")
    p = HLEN
    recipe = raw[p:p + rl]; p += rl
    runs = []
    for _ in range(count):
        (n,) = struct.unpack_from("<Q", raw, p)
        runs.append((n, raw[p + 8:p + 40].hex())); p += 40
    sig = raw[p:p + 64]; p += 64
    ots = raw[p:p + ol]
    return {"recipe_bytes": recipe, "recipe": json.loads(recipe), "runs": runs, "sig": sig, "ots": ots,
            "id": file_id(recipe, runs), "bytes": len(raw)}


# ------------------------------------------------------------------ Merkle over regenerated digits
def leaves(digits: str, upto: int, block: int = 1000):
    return [sha(f"{i + 1}:".encode() + digits[i:min(i + block, upto)].encode()) for i in range(0, upto, block)]


def levels_of(lv0):
    lv = [lv0]
    while len(lv[-1]) > 1:
        L = lv[-1]
        if len(L) % 2:
            L = L + [L[-1]]
        lv.append([sha(L[i] + L[i + 1]) for i in range(0, len(L), 2)])
    return lv


def root(digits: str, upto: int, block: int = 1000) -> str:
    return levels_of(leaves(digits, upto, block))[-1][0].hex()


def proof(levels, i):
    out = []
    for L in levels[:-1]:
        if len(L) % 2:
            L = L + [L[-1]]
        out.append(L[i ^ 1].hex())
        i //= 2
    return out
