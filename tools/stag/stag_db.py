#!/usr/bin/env python3
"""Tag the NAS photo library into a local SQLite DB with stag's model (RAM++) plus an NSFW score.

READ-ONLY on the NAS: never writes sidecars or edits files there (its SSD write cache
is degraded). Every result is keyed by the file's SHA-256 with the last path it was seen
at, so the ongoing Pictures -> home\\Photos\\Library move does not matter.

Models (local inference, GPU):
  - tags: RAM++ swin-L, the model stag 1.0.1 uses
    (xinyu1205/recognize-anything-plus-model, ram_plus_swin_large_14m.pth, snapshot
    84d4aee3a0265c4e0df1f714f0572011d1bf2ec3). Score = sigmoid of the tag logit; only tags
    over RAM++'s own per-class threshold are kept (same rule stag uses).
  - nsfw: Falconsai/nsfw_image_detection (ViT-base, Apache-2.0) pinned at revision
    96cb0d0342c7afb80cab76ecc58b265fa44da256. Stored as tag "nsfw" with P(nsfw).
Videos: 3 frames at 10/50/90 % via ffmpeg; per tag the max score over the frames.

Gentle: BelowNormal priority, one worker, small batches, waits for free VRAM before
loading (Ollama often holds ~10 GB), pauses while CPU is pegged, resumable (skips
known SHA-256s and unchanged paths), frees the GPU on exit.

Runs in D:\\tools\\stag\\venv (Python 3.12, torch 2.6.0+cu124) - the venv Garden for All
set up for stag 1.0.1 on 2026-11-13 (verified working: CUDA, RTX 4070).
"""
import argparse
import ctypes
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time

DEFAULT_DB = r"D:\tools\stag\tags.db"
RAM_REPO, RAM_FILE = "xinyu1205/recognize-anything-plus-model", "ram_plus_swin_large_14m.pth"
RAM_REV = "84d4aee3a0265c4e0df1f714f0572011d1bf2ec3"
NSFW_REPO, NSFW_REV = "Falconsai/nsfw_image_detection", "96cb0d0342c7afb80cab76ecc58b265fa44da256"
RAM_MODEL = f"ram_plus_swin_l@{RAM_REV[:7]}"
NSFW_MODEL = f"{NSFW_REPO}@{NSFW_REV[:7]}"

IMG_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".hif", ".tif", ".tiff", ".webp", ".bmp", ".gif"}
RAW_EXT = {".nef", ".cr2", ".cr3", ".arw", ".dng", ".raf", ".orf", ".rw2"}
VID_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".mts"}
SKIP_DIRS = {"#recycle", "@eaDir", ".tmp.drivedownload"}

log = logging.getLogger("stag_db")


# ---------- DB ----------
SCHEMA = """
CREATE TABLE IF NOT EXISTS tags (sha256 TEXT NOT NULL, path TEXT NOT NULL, tag TEXT NOT NULL,
    score REAL NOT NULL, model TEXT NOT NULL, ts TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS tags_sha ON tags(sha256);
CREATE INDEX IF NOT EXISTS tags_tag ON tags(tag);
-- one row per file content: resume key, last seen path, status (done / error:<msg>)
CREATE TABLE IF NOT EXISTS files (sha256 TEXT PRIMARY KEY, path TEXT NOT NULL, kind TEXT,
    status TEXT NOT NULL, ts TEXT NOT NULL);
-- path cache: lets a rerun skip unchanged paths without re-reading them over SMB
CREATE TABLE IF NOT EXISTS seen (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, sha256 TEXT);
"""


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def open_db(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("PRAGMA journal_mode=WAL")
    return db


# ---------- gentleness ----------
def below_normal_priority():
    if os.name == "nt":
        BELOW_NORMAL = 0x4000
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL)


class _FT(ctypes.Structure):
    _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]


def _sys_times():
    i, k, u = _FT(), _FT(), _FT()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
    f = lambda t: (t.hi << 32) | t.lo
    return f(i), f(k) + f(u)  # kernel time includes idle


def cpu_busy_pct(sample=1.0):
    if os.name != "nt":
        return 0.0
    i1, t1 = _sys_times(); time.sleep(sample); i2, t2 = _sys_times()
    total = t2 - t1
    return 100.0 * (1 - (i2 - i1) / total) if total else 0.0


def free_vram_mb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout
        return int(out.split()[0])
    except Exception:
        return -1


OLLAMA = "http://127.0.0.1:11434"  # the PC's Ollama (docker container "ollama", port bound to localhost)
ollama_unloads = 0


def ollama_unload():
    """Ask Ollama to drop every loaded model (keep_alive 0) so stag gets the VRAM.

    Only unloads; never stops or changes the jobs that use Ollama - their next request
    simply reloads the model. Returns the names that were loaded.
    """
    import urllib.request as u
    global ollama_unloads
    try:
        with u.urlopen(OLLAMA + "/api/ps", timeout=10) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
        for n in names:
            req = u.Request(OLLAMA + "/api/generate", data=json.dumps({"model": n, "keep_alive": 0}).encode(),
                            headers={"Content-Type": "application/json"})
            u.urlopen(req, timeout=60).read()
        if names:
            ollama_unloads += 1
            log.info("asked Ollama to unload %s", ", ".join(names))
        return names
    except Exception as e:
        log.warning("Ollama unload failed: %s", str(e)[:150])
        return []


def wait_for_vram(need_mb, should_stop, poll=60):
    """Unload Ollama and wait for need_mb free VRAM; None if a stop came first."""
    while True:
        ollama_unload()
        time.sleep(5)
        free = free_vram_mb()
        if free < 0 or free >= need_mb:
            return free
        if should_stop():
            log.warning("VRAM blocked: only %d MiB free (need %d) until stop time; giving up", free, need_mb)
            return None
        log.info("waiting for GPU: %d MiB free, need %d (another app holds VRAM)", free, need_mb)
        time.sleep(poll)


def make_stop(until, stop_file):
    """Return should_stop(): true after the HH:MM deadline or when stop_file exists."""
    import datetime as dt
    deadline = None
    if until:
        h, m = map(int, until.split(":"))
        n = dt.datetime.now()
        deadline = n.replace(hour=h, minute=m, second=0, microsecond=0)
        if deadline <= n:
            deadline += dt.timedelta(days=1)
        log.info("will stop at %s", deadline.strftime("%Y-%m-%d %H:%M"))
    return lambda: (deadline is not None and dt.datetime.now() >= deadline) or os.path.exists(stop_file)


def maybe_pause(cpu_limit):
    while (busy := cpu_busy_pct()) > cpu_limit:
        log.info("PC busy (CPU %.0f%% > %d%%), pausing 60 s", busy, cpu_limit)
        time.sleep(60)


# ---------- file discovery ----------
YEAR_RE = re.compile(r"^(19|20)\d\d$")


def iter_files(roots, all_dirs):
    """Yield (path, kind) under each root. Pictures-style roots: only YYYY folders unless --all-dirs."""
    for root in roots:
        if not os.path.isdir(root):
            log.warning("root not reachable, skipped: %s", root)
            continue
        top = sorted(os.listdir(root))
        for name in top:
            p = os.path.join(root, name)
            if os.path.isdir(p):
                if name in SKIP_DIRS or (not all_dirs and not YEAR_RE.match(name)):
                    continue
                for cur, dirs, files in os.walk(p):
                    dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
                    for f in sorted(files):
                        k = kind_of(f)
                        if k and not f.startswith("."):
                            yield os.path.join(cur, f), k
            elif all_dirs and kind_of(name):
                yield p, kind_of(name)


def kind_of(name):
    e = os.path.splitext(name)[1].lower()
    return "image" if e in IMG_EXT else "raw" if e in RAW_EXT else "video" if e in VID_EXT else None


def sha256_and_bytes(path, keep_bytes):
    h, buf = hashlib.sha256(), (io.BytesIO() if keep_bytes else None)
    with open(path, "rb") as fh:  # read-only
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            if buf is not None:
                buf.write(chunk)
    return h.hexdigest(), (buf.getvalue() if buf is not None else None)


# ---------- decoding ----------
def decode_image(data, kind):
    from PIL import Image, ImageOps
    if kind == "raw":
        import rawpy
        with rawpy.imread(io.BytesIO(data)) as raw:
            return Image.fromarray(raw.postprocess(half_size=True)).convert("RGB")
    im = Image.open(io.BytesIO(data))
    im.seek(0)
    im = ImageOps.exif_transpose(im)
    im.thumbnail((1024, 1024))  # models use 384/224; cut CPU + RAM
    return im.convert("RGB")


def video_frames(path, ffmpeg, ffprobe):
    from PIL import Image
    try:
        dur = float(subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of",
                                    "default=nw=1:nk=1", path], capture_output=True, text=True,
                                   timeout=120).stdout.strip())
    except Exception:
        return []
    frames = []
    for frac in (0.1, 0.5, 0.9):
        r = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{dur * frac:.2f}", "-i", path, "-frames:v", "1",
                            "-vf", "scale=768:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                           capture_output=True, timeout=300)
        if r.returncode == 0 and r.stdout:
            frames.append(Image.open(io.BytesIO(r.stdout)).convert("RGB"))
    return frames


# ---------- models ----------
class Models:
    def __init__(self, device):
        import torch
        from huggingface_hub import hf_hub_download
        from ram import get_transform
        from ram.models import ram_plus
        # Direct ViT classes: the Auto* lookup imports timm_wrapper, which fails on the old timm 0.4.12 RAM++ pins.
        from transformers import ViTForImageClassification, ViTImageProcessor
        self.torch, self.device = torch, device
        ckpt = hf_hub_download(RAM_REPO, RAM_FILE, revision=RAM_REV)
        self.ram_tf = get_transform(image_size=384)
        self.ram = ram_plus(pretrained=ckpt, image_size=384, vit="swin_l").eval().to(device)
        if device == "cuda":
            self.ram = self.ram.half()
        self.nsfw_proc = ViTImageProcessor.from_pretrained(NSFW_REPO, revision=NSFW_REV)
        self.nsfw = ViTForImageClassification.from_pretrained(NSFW_REPO, revision=NSFW_REV).eval().to(device)
        self.nsfw_idx = {v.lower(): int(k) for k, v in self.nsfw.config.id2label.items()}["nsfw"]

    def ram_scores(self, imgs):
        """RAM++ generate_tag() logic, but returns {tag: sigmoid score} for tags over threshold."""
        torch, m = self.torch, self.ram
        import torch.nn.functional as F
        x = torch.stack([self.ram_tf(i) for i in imgs]).to(self.device)
        if self.device == "cuda":
            x = x.half()
        with torch.no_grad():
            emb = m.image_proj(m.visual_encoder(x))
            atts = torch.ones(emb.size()[:-1], dtype=torch.long, device=x.device)
            cls = emb[:, 0, :]
            cls = cls / cls.norm(dim=-1, keepdim=True)
            bs, dpc = emb.shape[0], int(m.label_embed.shape[0] / m.num_class)
            lp = (m.reweight_scale.exp() * cls @ m.label_embed.t()).view(bs, -1, dpc)
            w = F.softmax(lp, dim=2)
            le = m.label_embed.view(-1, dpc, 512)
            # per-image loop like upstream: a batched broadcast costs ~240 MB VRAM per image in fp16
            lre = torch.stack([(w[b].unsqueeze(-1) * le).sum(dim=1) for b in range(bs)])
            lab = F.relu(m.wordvec_proj(lre))
            th = m.tagging_head(encoder_embeds=lab, encoder_hidden_states=emb, encoder_attention_mask=atts,
                                return_dict=False, mode="tagging")
            p = torch.sigmoid(m.fc(th[0]).squeeze(-1).float()).cpu().numpy()
        thr = m.class_threshold.float().cpu().numpy()
        out = []
        for b in range(bs):
            pb = p[b].copy()
            pb[m.delete_tag_index] = 0
            idx = (pb > thr).nonzero()[0]
            out.append({str(m.tag_list[i]): float(pb[i]) for i in idx})
        return out

    def nsfw_scores(self, imgs):
        torch = self.torch
        with torch.no_grad():
            inp = self.nsfw_proc(images=imgs, return_tensors="pt").to(self.device)
            pr = torch.softmax(self.nsfw(**inp).logits.float(), dim=-1)[:, self.nsfw_idx]
        return [float(v) for v in pr.cpu()]

    def unload(self):
        del self.ram, self.nsfw
        if self.device == "cuda":
            self.torch.cuda.empty_cache()


# ---------- main loop ----------
# The NAS is reached by its Tailscale IP (the LAN name has a stale saved login in this
# Windows session); store paths under the LAN name so they stay stable.
PATH_ALIASES = {"\\\\100.64.0.12\\": "\\\\192.168.1.68\\",
                "P:\\": "\\\\192.168.1.68\\home\\Photos\\"}  # P: = jay's home\Photos, mapped by the photo-move job


def norm_path(p):
    for k, v in PATH_ALIASES.items():
        if p.lower().startswith(k.lower()):
            return v + p[len(k):]
    return p


def save(db, sha, path, kind, tags, nsfw):
    ts, path = now(), norm_path(path)
    db.execute("DELETE FROM tags WHERE sha256=?", (sha,))
    db.executemany("INSERT INTO tags VALUES (?,?,?,?,?,?)",
                   [(sha, path, t, round(s, 4), RAM_MODEL, ts) for t, s in tags.items()] +
                   [(sha, path, "nsfw", round(nsfw, 4), NSFW_MODEL, ts)])
    db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)", (sha, path, kind, "done", ts))


def run(a):
    below_normal_priority()
    db = open_db(a.db)
    import shutil
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not (ffmpeg and ffprobe):
        log.warning("ffmpeg/ffprobe not found: videos skipped")
    should_stop = make_stop(a.until, a.stop_file)
    free = wait_for_vram(a.min_free_vram, should_stop) if a.device == "cuda" else -1
    if free is None:
        db.close()
        sys.exit(3)
    log.info("loading models on %s (free VRAM %s MiB)", a.device, free)
    models = Models(a.device)
    if a.device == "cuda":
        import torch
        log.info("models loaded, VRAM allocated by this process %.0f MiB", torch.cuda.memory_allocated() / 2**20)
    done_sha = {r[0] for r in db.execute("SELECT sha256 FROM files WHERE status='done'")}
    seen = {r[0]: (r[1], r[2], r[3]) for r in db.execute("SELECT path,size,mtime,sha256 FROM seen")}
    n_new = n_skip = n_err = 0
    t0 = time.time()
    batch = []  # (sha, path, kind, img)
    next_report = 200

    def flush():
        nonlocal n_new
        if not batch:
            return
        imgs = [b[3] for b in batch]
        if a.device == "cuda":
            ollama_unload()  # keep the GPU ours while we work (Jason's choice: stag at night)
        tg, ns = models.ram_scores(imgs), models.nsfw_scores(imgs)
        for (sha, path, kind, _), t, n in zip(batch, tg, ns):
            save(db, sha, path, kind, t, n)
            done_sha.add(sha)
            n_new += 1
        db.commit()
        batch.clear()

    try:
        for i, (path, kind) in enumerate(iter_files(a.roots, a.all_dirs)):
            if a.limit and n_new + len(batch) >= a.limit:
                break
            if should_stop():
                log.info("stop requested (deadline or stop file): finishing current batch")
                break
            if kind == "video" and not (ffmpeg and a.videos):
                continue
            sha = None
            try:
                st = os.stat(path)
                c = seen.get(path)
                if c and c[0] == st.st_size and c[1] == st.st_mtime and c[2] in done_sha:
                    n_skip += 1
                    continue
                sha, data = sha256_and_bytes(path, keep_bytes=(kind != "video"))
                db.execute("INSERT OR REPLACE INTO seen VALUES (?,?,?,?)", (path, st.st_size, st.st_mtime, sha))
                if sha in done_sha:  # same content seen at another path (moved/duplicate)
                    db.execute("UPDATE files SET path=? WHERE sha256=?", (norm_path(path), sha))
                    db.execute("UPDATE tags SET path=? WHERE sha256=?", (norm_path(path), sha))
                    n_skip += 1
                    continue
                if kind == "video":
                    frames = video_frames(path, ffmpeg, ffprobe)
                    if not frames:
                        raise RuntimeError("no frames")
                    if a.device == "cuda":
                        ollama_unload()
                    tg, ns = models.ram_scores(frames), models.nsfw_scores(frames)
                    merged = {}
                    for d in tg:
                        for t, s in d.items():
                            merged[t] = max(s, merged.get(t, 0))
                    save(db, sha, path, kind, merged, max(ns))
                    done_sha.add(sha); n_new += 1; db.commit()
                else:
                    batch.append((sha, path, kind, decode_image(data, kind)))
                    if len(batch) >= a.batch:
                        flush()
            except Exception as e:
                n_err += 1
                log.warning("error %s: %s", path, str(e)[:200])
                if sha:
                    db.execute("INSERT OR IGNORE INTO files VALUES (?,?,?,?,?)",
                               (sha, path, kind, "error:" + str(e)[:150], now()))
                db.commit()
            if n_new + n_skip >= next_report and not batch:
                next_report += 200
                el = time.time() - t0
                log.info("progress: new=%d skipped=%d errors=%d %.2f new/s", n_new, n_skip, n_err, n_new / el)
                if a.cpu_pause:
                    maybe_pause(a.cpu_pause)
        flush()
    finally:
        db.commit()
        if a.device == "cuda":
            import torch
            log.info("peak VRAM by this process %.0f MiB", torch.cuda.max_memory_allocated() / 2**20)
        models.unload()  # Jason's rule: free the GPU after use
        el = time.time() - t0
        log.info("finished: new=%d skipped=%d errors=%d in %.0f s (%.2f new/s); Ollama unload calls=%d; GPU freed",
                 n_new, n_skip, n_err, el, n_new / el if el else 0, ollama_unloads)
        db.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("roots", nargs="+", help="library roots (read-only)")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--log", default=r"D:\tools\stag\run.log")
    ap.add_argument("--limit", type=int, default=0, help="stop after N newly tagged files (test run)")
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--min-free-vram", type=int, default=3000, help="MiB free before loading models")
    ap.add_argument("--cpu-pause", type=int, default=90, help="pause while system CPU %% is above this (0=off)")
    ap.add_argument("--no-videos", dest="videos", action="store_false")
    ap.add_argument("--all-dirs", action="store_true", help="walk every folder, not only YYYY ones")
    ap.add_argument("--until", help="HH:MM: finish the current batch and exit at this time")
    ap.add_argument("--stop-file", default=r"D:\tools\stag\STOP", help="exit cleanly when this file exists")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(a.log, encoding="utf-8"), logging.StreamHandler(sys.stdout)])
    os.environ.setdefault("HF_HOME", r"D:\tools\stag\hf-cache")  # models already cached there
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    run(a)


if __name__ == "__main__":
    main()
