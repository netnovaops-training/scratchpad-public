#!/usr/bin/env python3
# =============================================================================
#  Pee-Wee Studio -- the workbench for the dial.
#
#  Run it:   streamlit run "C:\Jasons New Site\www\peewee\studio\app.py"
#  (or double-click run-studio.cmd in this folder)
#
#  Everything on the tribute site is a folder under people/ -- photos, a
#  person.json, story .txt files, narration .mp3s. This app is those folders
#  with handles on: a checklist of who is done and who is missing what, a
#  place to write the JSON and the text without touching a text editor, an
#  importer for photos you have enhanced, and an exporter for the ones you
#  are about to.
#
#  IT ONLY WRITES WHAT YOU SAVE. The site reads data/stations.json, which
#  only changes when you press "Publish to the site" (which runs build.py).
#  So you can edit all afternoon and nothing goes live until you say so.
# =============================================================================
import hashlib as _hashlib
import html as _html
import io
import ipaddress
import json
import json as _json
import os
import re
import re as _re
import socket
import subprocess
import sys
import urllib.parse as _up
import urllib.request as _ur
import zipfile
from pathlib import Path

import streamlit as st
from PIL import Image, ImageOps
from PIL import Image as _Image


def _http_only(target):
    """Return target (a URL string or urllib Request) if its scheme is http or https, else raise ValueError.

    urlopen also follows file:// and custom schemes (Bandit B310). Every caller here talks to an
    http(s) service, so anything else is a mistake or a planted URL. Added 2026-11-19.
    """
    url = str(getattr(target, "full_url", target))
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"refusing a non-http(s) URL: {url[:80]}")
    return target


def _public_only(url):
    """Return url if it is http(s) and its host resolves only to public internet addresses, else raise ValueError.

    2026-11-20 (CodeQL py/full-ssrf): the page and picture links here are pasted by a person or scraped
    from a page, so they could point at this machine or the home network (localhost, 192.168.x, a cloud
    metadata address). Ordinary public sites are unaffected; the Ollama/voice/search calls do not use this.
    """
    parts = _up.urlsplit(str(url))
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"refusing a non-http(s) URL: {str(url)[:80]}")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError(f"could not look up {parts.hostname}")
    for info in infos:
        if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
            raise ValueError(f"refusing a non-public address for {parts.hostname}")
    return str(url)


class _PublicRedirects(_ur.HTTPRedirectHandler):
    """Every redirect hop must be public too, or a public page could bounce us onto the home network."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _public_only(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_public(url, headers, timeout):
    """urlopen for a link from outside: checked first, and again on every redirect."""
    req = _ur.Request(_public_only(url), headers=headers)
    return _ur.build_opener(_PublicRedirects).open(req, timeout=timeout)  # nosec B310 - scheme and host checked above


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))                 # peewee-tools/
SITE = os.environ.get("PEEWEE_SITE") or os.path.normpath(
    os.path.join(ROOT, "..", "web", "public", "peewee"))
PEOPLE = os.path.join(SITE, "people")
WORK = os.path.join(HERE, "work")   # export/import staging, one folder per person
# The Legacy archive's web-size mirror, for browsing/downloading originals.
LEGACY = os.environ.get("LEGACY_SMALL") or os.path.normpath(
    os.path.join(ROOT, "..", "..", "Legacy-small"))
# Which folders/photos Jason has already been through -- his memory between
# sessions, one JSON file.
CLICKED = os.path.join(HERE, "clicked.json")

os.makedirs(WORK, exist_ok=True)


def clicked_load():
    try:
        with open(CLICKED, encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, ValueError):
        return {}


def clicked_save(d):
    with open(CLICKED, "w", encoding="utf-8", newline="\n") as f:
        _json.dump(d, f, indent=1)


OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DRAFT_MODEL = os.environ.get("DRAFT_MODEL", "gemma4:12b")


def fetch_text(url, limit=6000):
    """One page, stripped to readable text. Crude on purpose: a tag-stripper
    and whitespace squeeze cover news pages, obits and wikis well enough for
    drafting, with no dependency to rot."""
    with _open_public(url, {"User-Agent": "Mozilla/5.0 (peewee-studio)"}, 30) as r:
        raw = r.read(400_000).decode("utf-8", "replace")
    raw = _re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", raw)
    raw = _re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = _html.unescape(raw)
    return " ".join(raw.split())[:limit]



# ---------------------------------------------------------------------------
#  PICTURES OFF A LINK (Jason, 2026-09-02).
#
#  The same pages that get read for their words also carry photographs,
#  and hand-saving those was the slow half of adding somebody. These
#  pull the candidates and stage them; NOTHING lands in people/ until a
#  human ticks it on the review grid. A picture on a page is a claim
#  about who somebody is, and this app does not make that claim by
#  itself -- it only offers the shortlist.
# ---------------------------------------------------------------------------
IMG_SKIP = ("logo", "sprite", "icon", "avatar", "banner", "badge", "pixel",
            "spacer", "advert", "placeholder", "thumb-", "favicon")


def _img_candidates(page, base):
    """Every plausible image URL on a page, best-quality form first."""
    urls = []
    # what the page itself nominates as its picture
    for pat in (r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image'):
        urls += _re.findall(pat, page, _re.IGNORECASE)
    # srcset: take the largest candidate in each set
    for ss in _re.findall(r'srcset=["\']([^"\']+)["\']', page, _re.IGNORECASE):
        best, bestw = None, -1
        for bit in ss.split(","):
            parts = bit.strip().split()
            if not parts:
                continue
            w = 0
            if len(parts) > 1 and parts[1].endswith("w"):
                try:
                    w = int(parts[1][:-1])
                except ValueError:
                    w = 0
            if w > bestw:
                best, bestw = parts[0], w
        if best:
            urls.append(best)
    urls += _re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', page, _re.IGNORECASE)
    out, seen = [], set()
    for u in urls:
        u = _html.unescape(u.strip())
        if u.startswith("data:"):
            continue
        full = _up.urljoin(base, u)
        if not full.lower().startswith(("http://", "https://")):
            continue
        low = full.lower()
        if any(w in low for w in IMG_SKIP):
            continue
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


def fetch_images(url, want=14, min_side=220):
    """Candidate photographs from one page, as (url, bytes, w, h).

    Small images are dropped -- under a couple of hundred pixels a side
    is furniture, not a photograph. Duplicates are dropped by content, so
    the same picture served from two paths only appears once."""
    with _open_public(url, {"User-Agent": "Mozilla/5.0 (peewee-studio)"}, 30) as r:
        page = r.read(600_000).decode("utf-8", "replace")
    got, seen_bytes = [], set()
    for iu in _img_candidates(page, url):
        if len(got) >= want:
            break
        try:
            with _open_public(iu, {"User-Agent": "Mozilla/5.0 (peewee-studio)",
                                   "Referer": url}, 20) as r:
                blob = r.read(8_000_000)
        except Exception:  # noqa: BLE001, S112  scraped image: any fetch failure skips that image
            continue
        # sha256, not sha1 (2026-11-19): the hash only lives in this loop's seen-set, so changing it costs
        # nothing, and Semgrep's sha1 rule ignores nosemgrep in the SARIF upload.
        h = _hashlib.sha256(blob).hexdigest()  # a duplicate check, not security
        if h in seen_bytes:
            continue
        try:
            im = _Image.open(io.BytesIO(blob))
            im.verify()
            im = _Image.open(io.BytesIO(blob))
            w, ht = im.size
        except Exception:  # noqa: BLE001, S112  Pillow raises many types on junk bytes; skip it
            continue
        if w < min_side or ht < min_side:
            continue
        seen_bytes.add(h)
        got.append((iu, blob, w, ht))
    return got


def pulled_dir(slug):
    # 2026-11-20 (CodeQL py/path-injection): the staging folder must resolve to somewhere under WORK;
    # a slug like "../x" stops here with a ValueError instead of creating folders elsewhere.
    root = os.path.realpath(WORK)
    d = os.path.realpath(os.path.join(root, slug, "pulled"))
    if not slug or not d.startswith(root + os.sep):
        raise ValueError(f"not a staging folder: {slug!r}")
    os.makedirs(d, exist_ok=True)
    return d


def stage_images(slug, urls, want=14):
    """Pull from every link and stage the results. Returns what landed."""
    d = pulled_dir(slug)
    kept, n = [], 0
    for u in urls:
        try:
            found = fetch_images(u, want=want)
        except Exception as e:  # noqa: BLE001
            kept.append((None, f"could not read {u}: {e}"))
            continue
        for iu, blob, w, ht in found:
            n += 1
            ext = ".png" if blob[:8].startswith(b"\x89PNG") else ".jpg"
            fp = os.path.join(d, f"pull-{n:03d}{ext}")   # d is already checked by pulled_dir
            with open(fp, "wb") as f:
                f.write(blob)
            with open(fp + ".src.txt", "w", encoding="utf-8") as f:
                f.write(iu + "\n" + u + "\n")   # where it came from, kept
            kept.append((fp, f"{w}x{ht}"))
    return kept


def adopt_image(slug, path):
    """Move one staged picture into the person's folder as the next photo."""
    d = person_dir(slug)
    os.makedirs(d, exist_ok=True)
    n = 1
    while os.path.exists(os.path.join(d, f"photo-{n}.jpg")):
        n += 1
    im = _Image.open(path)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.save(os.path.join(d, f"photo-{n}.jpg"), "JPEG", quality=92)
    return f"photo-{n}.jpg"


def review_pulled(slug, key):
    """The grid: everything staged for this person, with a keep button.

    Deliberately manual. The app pulls the shortlist; a person decides
    which of them is actually the person."""
    d = pulled_dir(slug)
    shots = sorted(f for f in os.listdir(d)
                   if f.lower().endswith((".jpg", ".png")))
    if not shots:
        st.caption("nothing pulled yet for this person")
        return
    st.caption(f"{len(shots)} pulled - tick the ones that are actually them. "
               "Nothing here is on the site until you keep it.")
    picks = []
    cols = st.columns(4)
    for i, f in enumerate(shots):
        fp = os.path.join(d, f)
        with cols[i % 4]:
            st.image(fp, use_container_width=True)
            src = ""
            try:
                src = Path(fp + ".src.txt").read_text(encoding="utf-8").split("\n")[1]
            except (OSError, ValueError, IndexError):
                pass
            st.caption(src[:46] or f)
            if st.checkbox("keep", key=f"{key}-{f}"):
                picks.append(fp)
    c1, c2 = st.columns(2)
    if c1.button("Keep the ticked ones as photos", key=key + "-keep") and picks:
        made = [adopt_image(slug, fp) for fp in picks]
        for fp in picks:
            os.remove(fp)
            if os.path.exists(fp + ".src.txt"):
                os.remove(fp + ".src.txt")
        st.success("added " + ", ".join(made) + " - Publish puts them on the site")
    if c2.button("Discard everything pulled", key=key + "-clear"):
        for f in os.listdir(d):
            os.remove(os.path.join(d, f))
        st.info("staging cleared")


def era_search(name, limit=3):
    """Period findings folded into the sources: a DuckDuckGo query aimed at
    the man's decades, top results fetched like any pasted link. The
    no-invention rule is untouched -- these become SOURCES the model may
    quote, not license to imagine."""
    q = _ur.quote(f'{name} 1950s 1960s 1970s')
    try:
        req = _ur.Request("https://html.duckduckgo.com/html/?q=" + q,
                          headers={"User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(_http_only(req), timeout=20) as r:  # nosec B310
            page = r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001  web search is optional; no results on any failure
        return []
    urls = _re.findall(r'class="result__a"[^>]+href="([^"]+)"', page)
    # DDG wraps results in a redirect; unwrap the real target
    out = []
    for u in urls:
        m = _re.search(r'uddg=([^&]+)', u)
        real = _ur.unquote(m.group(1)) if m else u
        if real.startswith("http") and "duckduckgo" not in real:
            out.append(real)
        if len(out) >= limit:
            break
    found = []
    for u in out:
        try:
            found.append((u, fetch_text(u)))
        except Exception:  # noqa: BLE001, S110  one unreadable page is skipped
            pass
    return found


def draft_tellings(name, sources, n):
    """n variations of the person's story, from the pages Jason chose.
    The model is instructed to use ONLY what the sources say -- the same
    no-invention rule the whole site runs on -- and every result lands as a
    DRAFT for editing before anyone hears it."""
    material = "\n\n---\n\n".join(
        f"SOURCE {i+1} ({u}):\n{t}" for i, (u, t) in enumerate(sources))
    prompt = (
        f"You write for a radio tribute site about people connected to Edward "
        f"'Pee-Wee' Harris, a pioneering Black DJ in Albany NY (1923-1992). "
        f"Using ONLY facts present in the sources below, write {n} DIFFERENT "
        f"tellings about {name}: each 2-3 paragraphs, warm broadcast prose. "
        f"VOICE: you ARE the host. Pee-Wee worked the 1950s through the "
        f"1980s, before the internet -- a jock knew what he knew from the "
        f"trade papers, the charts, the record men and the artists who came "
        f"through. Tell it that way: present-tense warmth for the era's "
        f"moments, chart numbers and years the way a jock would call them, "
        f"as if he was there telling it himself. Never claim Pee-Wee met "
        f"or knew a person unless a source says so. "
        f"STRUCTURE (Jason's 2026-09-01 spec) -- Announce the person first: "
        f"open with the name alone as its own sentence. Then born date, and "
        f"sunset date if the sources hold one. Then what they WERE -- "
        f"musician, entrepreneur, president, movie star -- in one plain "
        f"sentence. Then the highlights: what they did and what they "
        f"brought to the spotlight. Every sentence lands with a full stop; "
        f"the last sentence must read as an ENDING, never a trail-off. "
        f"Each telling "
        f"DEPTH IS THE POINT (Jason, 2026-09-01): these are famous people "
        f"and the page should tell somebody something they did not "
        f"know. Wherever the sources give one, use the SPECIFIC -- the "
        f"year, the label, the chart position, the award and the night "
        f"it was won, the room it was recorded in, the person who "
        f"opened the door, what it cost them. Numbers and names beat "
        f"adjectives every time. Never pad with praise; if the sources "
        f"are thin, write less rather than write air. "
        f"Each telling "
        f"must stand alone and differ in angle and opening. Do not invent "
        f"facts absent from the sources. Do not mention the sources. "
        f"These tellings are READ ALOUD by a cloned voice, never shown as "
        f"text, so write for the ear, dictation-first: plain flowing prose "
        f"a broadcaster could read cold. No emojis, no markdown, no "
        f"bullet points, no headings, no parentheses, no slashes, no "
        f"abbreviations that sound awkward spoken. Prefer words over "
        f"symbols: 'and', 'or', 'to'. Punctuation is stage direction: "
        f"commas are breaths, periods are full stops, at most one "
        f"exclamation point where real excitement belongs. If a word "
        f"could be mispronounced, spell it the way it sounds. "
        f"Separate the tellings with a line containing only =====.\n\n{material}"
    )
    body = _json.dumps({
        "model": DRAFT_MODEL, "stream": False,
        "options": {"temperature": 0.8},
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = _ur.Request(OLLAMA + "/api/chat", data=body,
                      headers={"Content-Type": "application/json"})
    with _ur.urlopen(_http_only(req), timeout=900) as r:  # nosec B310
        text = _json.loads(r.read())["message"]["content"]
    parts = [t.strip() for t in text.split("=====") if len(t.strip()) > 200]
    return parts[:n]

st.set_page_config(page_title="Pee-Wee Studio", page_icon="📻", layout="wide")

JSON_FIELDS = ["name", "freq", "era", "relation", "caption"]


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def person_dir(slug):
    """people/<slug>, refused unless it is a real sub-folder of PEOPLE.

    2026-11-19 (CodeQL py/path-injection): slugs come from slugify() or folder listings, so they were
    already plain; an empty slug, though, meant PEOPLE itself. The resolved path must now sit strictly
    inside PEOPLE, so a bad slug stops here instead of touching the wrong folder.
    """
    root = os.path.realpath(PEOPLE)
    d = os.path.realpath(os.path.join(root, slug))
    if not slug or not d.startswith(root + os.sep):
        raise ValueError(f"not a person folder: {slug!r}")
    return d


def load(slug):
    with open(os.path.join(PEOPLE, slug, "person.json"), encoding="utf-8") as f:
        return json.load(f)


def save(slug, data):
    # 2026-11-20 (CodeQL py/path-injection): person_dir() refuses a slug that resolves outside PEOPLE.
    with open(os.path.join(person_dir(slug), "person.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1)


def files_of(slug, kind):
    d = person_dir(slug)
    if kind == "photos":
        return sorted(f for f in os.listdir(d) if re.fullmatch(r"photo-\d+\.jpg", f))
    if kind == "stories":
        return sorted(f for f in os.listdir(d) if re.fullmatch(r"story-\d+\.txt", f))
    if kind == "audio":
        return sorted(f for f in os.listdir(d) if re.fullmatch(r"story-\d+\.mp3", f))


def status_row(slug):
    p = load(slug)
    photos = files_of(slug, "photos")
    stories = files_of(slug, "stories")
    audio = files_of(slug, "audio")
    ps = p.get("photo_status", {})
    enhanced = sum(1 for f in photos if ps.get(f) == "enhanced")
    missing = [f for f in JSON_FIELDS if not p.get(f)]
    if not stories:
        missing.append("stories")
    if not photos:
        missing.append("photos")
    return {
        "person": p.get("name", slug),
        "slug": slug,
        "on the dial": "no (disabled)" if p.get("enabled") is False
                       else ("draft" if p.get("draft", True) else "yes"),
        "photos": f"{len(photos)} ({enhanced} enhanced)",
        "stories": len(stories),
        "narrated": f"{len(audio)}/{len(stories)}",
        "missing": ", ".join(missing) if missing else "\u2014 complete",
    }


slugs = sorted(d for d in os.listdir(PEOPLE)
               if os.path.isdir(os.path.join(PEOPLE, d))
               and os.path.exists(os.path.join(PEOPLE, d, "person.json")))

st.title("📻 Pee-Wee Studio")
(tab_roster, tab_list, tab_edit, tab_new, tab_legacy, tab_who, tab_voice,
 tab_publish) = st.tabs(
    ["The roster", "The checklist", "Work on somebody", "Add somebody",
     "Legacy photos", "Who is this?", "Voice lab", "Publish"])

# ---------------------------------------------------------------------------
with tab_list:
    st.caption("Who is done, who is missing what. 'Enhanced' counts the "
               "photos you have marked as finished on the Work tab.")
    st.dataframe([status_row(s) for s in slugs],
                 use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
with tab_edit:
    slug = st.selectbox("Person", slugs,
                        format_func=lambda s: load(s).get("name", s))
    if slug:
        p = load(slug)
        d = person_dir(slug)

        st.subheader("Who they are")
        c1, c2 = st.columns(2)
        p["name"] = c1.text_input("Name", p.get("name", ""))
        p["freq"] = c2.text_input("Frequency on the dial", p.get("freq", ""))
        p["era"] = st.text_input("Era line (under the name)", p.get("era", ""))
        p["relation"] = st.text_area("The Pee-Wee connection", p.get("relation", "") or "", height=80)
        p["caption"] = st.text_input("Photo caption", p.get("caption", "") or "")
        bridges = st.text_area("Bridge lines (one per line -- the segue when the "
                               "dial arrives here)",
                               "\n".join(p.get("bridges", [])), height=80)
        p["bridges"] = [b.strip() for b in bridges.split("\n") if b.strip()]
        c3, c4 = st.columns(2)
        p["draft"] = c3.checkbox("Still a draft", p.get("draft", True))
        enabled = c4.checkbox("On the dial", p.get("enabled", True) is not False)
        p["enabled"] = bool(enabled)

        st.subheader("The tellings")
        for f in files_of(slug, "stories"):
            path = os.path.join(d, f)
            txt = Path(path).read_text(encoding="utf-8")
            new = st.text_area(f + ("   \U0001F50A narrated" if
                               os.path.exists(path.replace(".txt", ".mp3"))
                               else "   (not yet narrated)"),
                               txt, height=180, key=f"story-{slug}-{f}")
            if new != txt:
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(new)
                mp3 = path.replace(".txt", ".mp3")
                # The words changed, so the old narration no longer matches;
                # narrate.py re-renders anything whose text is newer.
                st.info(f"{f} saved -- narration will re-render on next Publish")
        if st.button("Add another telling"):
            n = len(files_of(slug, "stories")) + 1
            Path(os.path.join(d, f"story-{n}.txt")).write_text("Write the new telling here.", encoding="utf-8", newline="\n")
            st.rerun()

        # ---- pictures off a link (2026-09-02) ------------------------
        st.subheader("Pictures pulled from links")
        st.caption("Paste a page about them. The Studio keeps the "
                   "photographs it finds so you can pick, instead of "
                   "saving each one by hand. Nothing goes on the site "
                   "until you tick it.")
        pull_raw = st.text_area("Links to pull pictures from, one per line",
                                "", height=80, key="pull-urls")
        if st.button("Pull the pictures") and pull_raw.strip():
            us = [u.strip() for u in pull_raw.splitlines() if u.strip()][:6]
            with st.spinner("reading those pages..."):
                try:
                    landed = stage_images(slug, us)
                except Exception as e:  # noqa: BLE001
                    landed = []
                    st.error(f"pull failed: {e}")
            for fp, note in landed:
                if fp is None:
                    st.warning(note)
            st.success(f"{len([x for x in landed if x[0]])} pictures staged below")
        review_pulled(slug, "edit-pull")
        st.divider()

        st.subheader("Photographs")
        ps = p.setdefault("photo_status", {})
        cols = st.columns(4)
        for i, f in enumerate(files_of(slug, "photos")):
            with cols[i % 4]:
                st.image(os.path.join(d, f), caption=f, use_container_width=True)
                done = st.checkbox("enhanced", ps.get(f) == "enhanced",
                                   key=f"ps-{slug}-{f}")
                ps[f] = "enhanced" if done else "raw"
                st.download_button("download", Path(os.path.join(d, f)).read_bytes(),
                                   file_name=f"{slug}-{f}", key=f"dl-{slug}-{f}")

        up = st.file_uploader("Import photos (added as the next photo-N, or "
                              "replaces if you upload one named photo-N.jpg)",
                              type=["jpg", "jpeg", "png", "webp"],
                              accept_multiple_files=True, key=f"up-{slug}")
        if up:
            for uf in up:
                im = ImageOps.exif_transpose(Image.open(uf)).convert("RGB")
                im.thumbnail((1600, 1600))
                m = re.fullmatch(r"photo-(\d+)\.\w+", uf.name)
                if m:
                    out = f"photo-{m.group(1)}.jpg"
                else:
                    nums = [int(re.findall(r"\d+", x)[0]) for x in files_of(slug, "photos")] or [0]
                    out = f"photo-{max(nums) + 1}.jpg"
                im.save(os.path.join(d, out), quality=88, optimize=True)
            st.success("imported -- press R to refresh the page")

        c5, c6, c7 = st.columns(3)
        if c5.button("\U0001F4BE Save this person", type="primary"):
            save(slug, p)
            st.success("saved")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for f in os.listdir(d):
                z.write(os.path.join(d, f), f)
        c6.download_button("\U0001F4E6 Export whole folder (zip)", buf.getvalue(),
                           file_name=f"{slug}.zip")
        # Desktop only: inside the container there is no Explorer to open
        # (and no button is drawn there: `and` stops before c7.button).
        if hasattr(os, "startfile") and c7.button("Open work folder for this person"):
            wd = os.path.join(WORK, slug)
            os.makedirs(wd, exist_ok=True)
            os.startfile(wd)

# ---------------------------------------------------------------------------
with tab_new:
    st.caption("A person is a folder. This makes the folder.")
    name = st.text_input("Name")
    freq = st.text_input("Frequency (e.g. AM 1050)", "AM 1050")
    era = st.text_input("Era line", "")
    relation = st.text_area("The Pee-Wee connection", "", height=70)
    first = st.text_area("First telling (2-3 paragraphs; you can edit later)",
                         "", height=160)
    st.divider()
    st.subheader("Or let the model draft it")
    st.caption("Paste 4-5 links about the person (news, obituaries, wiki). "
               "The model reads ONLY those pages and writes the variations as "
               "DRAFTS -- nothing goes on air until you have edited and "
               "published. Same no-invention rule as the whole site.")
    urls_raw = st.text_area("Web links, one per line", "", height=110,
                            key="draft-urls")
    n_var = st.slider("How many variations", 3, 5, 3)
    want_era = st.checkbox(
        "Also search the era (DuckDuckGo: the '50s-'70s record, folded in "
        "as extra sources)", key="era-search")
    want_pics = st.checkbox(
        "Also pull the pictures off those pages (staged for review, "
        "nothing published)", value=True, key="pull-pics")
    if st.button("Fetch links and draft the tellings") and name.strip() and urls_raw.strip():
        slug = slugify(name)
        d = person_dir(slug)
        urls = [u.strip() for u in urls_raw.splitlines() if u.strip()][:5]
        sources = []
        prog = st.progress(0.0, "reading the pages...")
        for i, u in enumerate(urls):
            try:
                sources.append((u, fetch_text(u)))
            except Exception as e:  # noqa: BLE001  one unreadable page is shown as a warning
                st.warning(f"could not read {u}: {e}")
            prog.progress((i + 1) / (len(urls) + 1), f"read {i+1}/{len(urls)}")
        # the same pages carry photographs -- keep them for review
        if want_pics:
            prog.progress(0.9, "pulling pictures off those pages...")
            try:
                landed = stage_images(slug, urls)
                ok = [x for x in landed if x[0]]
                st.info(f"{len(ok)} pictures pulled - review them on the "
                        "Work tab, under 'Pictures pulled from links'.")
            except Exception as e:  # noqa: BLE001
                st.warning(f"picture pull failed: {e}")
        if not sources:
            st.error("none of the links could be read")
        else:
            prog.progress(0.95, f"{DRAFT_MODEL} is writing {n_var} tellings "
                                "(a minute or two)...")
            try:
                if want_era:
                    extra = era_search(name.strip())
                    if extra:
                        st.caption(f"era search added {len(extra)} sources: "
                                   + ", ".join(u for u, _ in extra))
                    sources = sources + extra
                tellings = draft_tellings(name.strip(), sources, n_var)
            except Exception as e:  # noqa: BLE001  model call: any failure is shown to the user
                tellings = []
                st.error(f"the model call failed: {e}")
            if tellings:
                os.makedirs(d, exist_ok=True)
                if not os.path.exists(os.path.join(d, "person.json")):
                    save(slug, {"name": name.strip(), "freq": freq,
                                "era": era, "relation": relation,
                                "bridges": [], "draft": True, "enabled": True,
                                "sources": [u for u, _ in sources]})
                start = len(files_of(slug, "stories"))
                for i, t in enumerate(tellings, start + 1):
                    with open(os.path.join(d, f"story-{i}.txt"), "w",
                              encoding="utf-8", newline="\n") as f:
                        f.write(t)
                prog.progress(1.0, "done")
                st.success(f"{len(tellings)} draft tellings written to "
                           f"people/{slug}/ -- edit them on the Work tab, "
                           "add photos, then Publish narrates them.")

    if st.button("Create", type="primary") and name.strip():
        slug = slugify(name)
        d = person_dir(slug)
        if os.path.exists(d):
            st.error("that folder already exists")
        else:
            os.makedirs(d)
            save(slug, {"name": name.strip(), "freq": freq, "era": era,
                        "relation": relation, "bridges": [], "draft": True,
                        "enabled": True})
            Path(os.path.join(d, "story-1.txt")).write_text(first or "Write the first telling here.", encoding="utf-8", newline="\n")
            st.success(f"created people/{slug}/ -- find them on the Work tab")

# ---------------------------------------------------------------------------
with tab_legacy:
    st.caption("The whole Legacy archive at web size, folder by folder. The "
               "checkbox is your memory: tick a folder (or a single photo) "
               "when you have been through it, and the tick survives between "
               "sessions. Downloads come from this mirror; the untouched "
               "originals stay in Legacy/.")
    if not os.path.isdir(LEGACY):
        st.error(f"Legacy-small not found at {LEGACY}")
    else:
        clicked = clicked_load()
        # EVERY folder at EVERY depth. The archive's two biggest boxes --
        # AI_Restored_Faces (1,674 restored faces) and 'needs sorting'
        # (1,647) -- live a level down inside Photos-1-001, and a picker
        # that only saw the top level made them unreachable. Tool-junk
        # directories (python envs) are excluded by name.
        SKIP = {".venv", "ai_env", "__pycache__"}
        folders = ["(top level)"]
        for dirpath, dirs, _files in os.walk(LEGACY):
            dirs[:] = sorted(d for d in dirs if d not in SKIP)
            for d in dirs:
                rel = os.path.relpath(os.path.join(dirpath, d), LEGACY)
                folders.append(rel.replace(os.sep, "/"))
        # folder cards with done-ticks in the picker itself
        def label(f):
            mark = "✅ " if clicked.get("folder:" + f) else ""
            n = 0
            fd = LEGACY if f == "(top level)" else os.path.join(LEGACY, f)
            try:
                n = sum(1 for x in os.listdir(fd)
                        if x.lower().endswith((".jpg", ".jpeg", ".png")))
            except OSError:
                pass
            return f"{mark}{f}  ({n} photos)"
        sel = st.selectbox("Folder", folders, format_func=label)
        fdir = LEGACY if sel == "(top level)" else os.path.join(LEGACY, sel)

        done_key = "folder:" + sel
        done = st.checkbox("I have been through this folder",
                           bool(clicked.get(done_key)), key="fold-" + sel)
        if done != bool(clicked.get(done_key)):
            clicked[done_key] = done
            clicked_save(clicked)

        imgs = sorted(f for f in os.listdir(fdir)
                      if f.lower().endswith((".jpg", ".jpeg", ".png")))
        subs = sorted(d for d in os.listdir(fdir)
                      if os.path.isdir(os.path.join(fdir, d)))
        if subs:
            st.write("Subfolders here: " + ", ".join(
                ("✅ " if clicked.get(f"folder:{sel}/{d}") else "") + d
                for d in subs))
        # 1,600 thumbnails in one page would flatten any browser: pages of
        # sixty, with the tick-state global so page position never matters.
        PER = 60
        pages = max(1, (len(imgs) + PER - 1) // PER)
        pg = 1
        if pages > 1:
            pg = st.number_input(f"Page (of {pages}, {len(imgs)} photos)",
                                 1, pages, 1, key="pg-" + sel)
        page_imgs = imgs[(pg - 1) * PER: pg * PER]
        st.write(f"{len(imgs)} photos" + (f" — showing {len(page_imgs)}" if pages > 1 else ""))
        cols = st.columns(5)
        for i, f in enumerate(page_imgs):
            keyp = f"photo:{sel}/{f}"
            with cols[i % 5]:
                st.image(os.path.join(fdir, f), use_container_width=True)
                c1, c2 = st.columns([1, 1])
                seen = c1.checkbox("seen", bool(clicked.get(keyp)),
                                   key="ck-" + keyp)
                if seen != bool(clicked.get(keyp)):
                    clicked[keyp] = seen
                    clicked_save(clicked)
                c2.download_button("⬇", Path(os.path.join(fdir, f)).read_bytes(),
                                   file_name=f, key="dl-" + keyp)
        if imgs:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for f in imgs:
                    z.write(os.path.join(fdir, f), f)
            st.download_button("📦 Download this whole folder (zip)",
                               buf.getvalue(),
                               file_name=(sel.replace("/", "-") or "top") + ".zip")

# ---------------------------------------------------------------------------
with tab_publish:
    st.caption("Nothing reaches the site until you press these.")
    if st.button("\U0001F399 Narrate anything new or changed (needs the voice "
                 "service; takes ~a minute per story)"):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "narrate.py")],
                           capture_output=True, text=True, check=False)
        st.code((r.stdout + r.stderr)[-2000:])
    if st.button("\U0001F4E1 Publish to the site (build.py)", type="primary"):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "build.py")],
                           capture_output=True, text=True, check=False)
        st.code((r.stdout + r.stderr)[-2000:])
        if r.returncode == 0:
            st.success("live at https://peewee.home.example.net")
    st.caption("Then commit in git as usual, or ask Claude to.")

    st.subheader("The record crate")
    st.caption("Dropped new mp3s into music/? This refreshes the playlist, "
               "looks each new record up (Wikipedia, brief facts only -- "
               "genre, chart peaks; nothing invented), and renders the "
               "host's announcement clips for it. Existing records are "
               "left exactly as they are.")
    if st.button("\U0001F4BF Onboard new records"):
        music_dir = os.path.join(SITE, "music")
        files = sorted(f for f in os.listdir(music_dir)
                       if f.endswith(".mp3"))
        with open(os.path.join(SITE, "data", "music.json"), "w",
                  encoding="utf-8") as fh:
            _json.dump(files, fh, indent=1)
        st.write(f"playlist: {len(files)} records")
        for tool in ("songscrape.py", "patter.py"):
            with st.spinner(f"running {tool}..."):
                r = subprocess.run(
                    [sys.executable, os.path.join(ROOT, tool)],
                    capture_output=True, text=True, check=False)
            st.code((r.stdout + r.stderr)[-1500:])
        st.success("crate onboarded -- new records have facts and a voice")


# ---------------------------------------------------------------------------
#  Voice lab (2026-08-31, Jason's spec): tune how the voices SAY things.
#
#  Two rooms. "Try some dialogue" renders anything in any voice on either
#  engine -- for testing and tuning the models. "Fix a telling" is for when
#  a narration stumbles: respell the words the way they SOUND (punctuation
#  is stage direction -- commas breathe, an exclamation point performs),
#  render takes until one is right, then attach it to the person's profile.
#
#  The spoken version is saved as story-N.speak.txt BESIDE the display text,
#  never over it -- the site keeps showing correct spelling while the voice
#  reads the phonetic script. narrate.py prefers the .speak.txt from then on,
#  so batch re-renders keep the fix. NOTE: later edits to the display .txt
#  do NOT update an existing spoken override -- revisit the lab if the words
#  themselves change.
# ---------------------------------------------------------------------------
with tab_voice:
    VOICE_FAST = os.environ.get(
        "VOICE_URL", "http://voice:8091/v1/audio/speech")
    VOICE_EXPR = os.environ.get(
        "VOICE_NARRATE_URL", "http://voice-narrate:8091/v1/audio/speech")

    def lab_voices():
        try:
            base = VOICE_FAST.split("/v1/")[0]
            with _ur.urlopen(_http_only(base + "/voices"), timeout=10) as r:  # nosec B310
                got = _json.load(r)
            names = got.get("voices") if isinstance(got, dict) else got
            return [n for n in names if not n.endswith("-old-30s")
                    and not n.endswith("-full-90s")] or ["Randy", "Jason"]
        except Exception:  # noqa: BLE001  voice list is optional; fall back to the two known voices
            return ["Randy", "Jason"]

    def lab_render(text, voice, url, opts=None):
        payload = {"input": text, "voice": voice,
                   "response_format": "mp3", "normalize": True}
        for k, v in (opts or {}).items():
            if v is not None:
                payload[k] = v
        body = _json.dumps(payload).encode("utf-8")
        req = _ur.Request(url, data=body,
                          headers={"Content-Type": "application/json"})
        with _ur.urlopen(_http_only(req), timeout=600) as r:  # nosec B310
            return r.read()

    st.caption("Punctuation is stage direction here: commas breathe, an "
               "exclamation point performs, spell words the way they sound. "
               "Nothing touches the site until you attach a take.")
    c1, c2 = st.columns(2)
    voice_pick = c1.selectbox("Voice", lab_voices(), key="lab-voice")
    engine_pick = c2.radio(
        "Engine", ["Expressive (the tribute's)", "Fast (everyday)"],
        horizontal=True, key="lab-engine")
    url_pick = (VOICE_EXPR if engine_pick.startswith("Expressive")
                else VOICE_FAST)

    st.subheader("Try some dialogue")
    scratch = st.text_area("Anything -- hear how this voice handles it",
                           "", height=100, key="lab-scratch")
    if st.button("Speak it", key="lab-speak") and scratch.strip():
        with st.spinner("rendering..."):
            try:
                st.session_state["lab_scratch_audio"] = lab_render(
                    scratch, voice_pick, url_pick)
            except Exception as e:  # noqa: BLE001
                st.error(f"render failed: {e}")
    if st.session_state.get("lab_scratch_audio"):
        st.audio(st.session_state["lab_scratch_audio"], format="audio/mp3")

    st.subheader("Announcer review")
    st.caption("Every clip the host owns, in BOTH voices side by side -- "
               "listen, and re-roll whichever take falls short. A re-roll "
               "deletes that one file; the next render pass (Publish "
               "onboarding, or the generators) re-creates it fresh.")
    with st.expander("Review Randy vs Jason, clip by clip"):
        rv_kind = st.selectbox("Clip set", [
            "handoffs (people/<slug>/handoff-*.mp3)",
            "patter (data/patter/*.mp3)",
            "tellings (people/<slug>/story-*.mp3)"], key="rv-kind")
        base_files = []
        if rv_kind.startswith("patter"):
            pd = os.path.join(SITE, "data", "patter")
            base_files = sorted(os.path.join("data", "patter", f)
                                for f in os.listdir(pd)
                                if f.endswith(".mp3") and "-jason" not in f)
        else:
            want = "handoff-" if rv_kind.startswith("handoffs") else "story-"
            for slug in sorted(os.listdir(PEOPLE)):
                d = person_dir(slug)
                if not os.path.isdir(d):
                    continue
                for f in sorted(os.listdir(d)):
                    if f.startswith(want) and f.endswith(".mp3")                             and "-jason" not in f and ".speak" not in f:
                        base_files.append(os.path.join("people", slug, f))
        st.caption(f"{len(base_files)} clips in this set")
        rv_pos = st.number_input("Clip", 1, max(1, len(base_files)), 1,
                                 key="rv-pos")
        if base_files:
            rel = base_files[rv_pos - 1]
            st.code(rel)
            for label, path in [
                ("Randy", os.path.join(SITE, rel)),
                ("Jason", os.path.join(SITE,
                    rel[:-4] + "-jason.mp3"))]:
                c1, c2 = st.columns([4, 1])
                with c1:
                    if os.path.exists(path):
                        st.caption(label)
                        st.audio(path)
                    else:
                        st.caption(f"{label}: not rendered yet")
                with c2:
                    if os.path.exists(path) and st.button(
                            "re-roll", key=f"rv-{label}-{rel}"):
                        os.remove(path)
                        st.success(f"{label} take deleted -- next render "
                                   "pass re-creates it")

    st.subheader("Fix a telling")
    lab_people = sorted(dd for dd in os.listdir(PEOPLE)
                        if os.path.isdir(os.path.join(PEOPLE, dd)))
    who = st.selectbox("Person", lab_people, key="lab-person")
    lab_stories = files_of(who, "stories")
    if not lab_stories:
        st.info("No tellings yet for this person -- write one on the "
                "Work tab first.")
    else:
        which = st.selectbox("Telling", lab_stories, key="lab-story")
        tpath = os.path.join(PEOPLE, who, which)
        spath = tpath.replace(".txt", ".speak.txt")
        spoken0 = Path(spath if os.path.exists(spath) else tpath).read_text(encoding="utf-8")
        if os.path.exists(spath):
            st.caption("This telling already has a spoken version -- you "
                       "are editing that, not the display text.")
        spoken = st.text_area(
            "How it should be SPOKEN (the site keeps showing the original "
            "text; this script is only for the voice)",
            spoken0, height=180, key=f"lab-spoken-{who}-{which}")

        # ---- the mixing desk: per-artist tuning, saved WITH the artist ----
        sfile = os.path.join(PEOPLE, who, "voice-settings.json")
        try:
            with open(sfile, encoding="utf-8") as fh:
                tune = _json.load(fh)
        except (OSError, ValueError):
            tune = {}
        st.markdown("**Fine-tune this artist's sound** -- sliders apply to "
                    "takes below; *Save tuning* keeps them with the artist "
                    "so batch re-renders use them too.")
        s1, s2, s3 = st.columns(3)
        t_speed = s1.slider("Pace (1.0 = natural)", 0.50, 1.50,
                            float(tune.get("speed", 1.0)), 0.01,
                            key=f"tune-speed-{who}")
        t_steps = s2.slider("Steps (fast engine only)", 2, 32,
                            int(tune.get("steps", 16)), 2,
                            key=f"tune-steps-{who}")
        t_gap = s3.slider("Paragraph pause (s)", 0.0, 2.0,
                          float(tune.get("gap", 0.65)), 0.05,
                          key=f"tune-gap-{who}")
        s4, s5 = st.columns(2)
        t_exag = s4.slider("Emotion exaggeration (expressive only)",
                           0.0, 1.0, float(tune.get("exaggeration", 0.5)),
                           0.05, key=f"tune-exag-{who}")
        t_cfg = s5.slider("Delivery weight (expressive only; lower = "
                          "slower, more deliberate)", 0.0, 1.0,
                          float(tune.get("cfg_weight", 0.5)), 0.05,
                          key=f"tune-cfg-{who}")
        lab_opts = {"speed": t_speed, "steps": t_steps, "gap": t_gap,
                    "exaggeration": t_exag, "cfg_weight": t_cfg}
        if st.button("Save tuning for this artist", key=f"tune-save-{who}"):
            keep = dict(lab_opts)
            keep["voice"] = voice_pick
            keep["engine"] = ("expressive"
                              if engine_pick.startswith("Expressive")
                              else "fast")
            with open(sfile, "w", encoding="utf-8", newline="\n") as fh:
                _json.dump(keep, fh, indent=2)
            st.success(f"tuning saved with {who} -- every future render "
                       "of this artist uses it")

        with st.expander("Edit the display text (what the site shows)"):
            disp0 = Path(tpath).read_text(encoding="utf-8")
            disp = st.text_area("story text", disp0, height=160,
                                key=f"lab-disp-{who}-{which}")
            if disp != disp0 and st.button("Save display text",
                                           key=f"lab-disp-save-{who}-{which}"):
                with open(tpath, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(disp)
                st.success("display text saved -- narration re-renders on "
                           "next Publish (spoken override, if any, still "
                           "wins the microphone)")
        with st.expander("Edit person.json"):
            jpath = os.path.join(PEOPLE, who, "person.json")
            try:
                j0 = Path(jpath).read_text(encoding="utf-8")
            except (OSError, ValueError):
                j0 = "{}"
            jraw = st.text_area("person.json", j0, height=200,
                                key=f"lab-json-{who}")
            if jraw != j0 and st.button("Save person.json",
                                        key=f"lab-json-save-{who}"):
                try:
                    _json.loads(jraw)
                except Exception as e:  # noqa: BLE001
                    st.error(f"not valid JSON, nothing saved: {e}")
                else:
                    with open(jpath, "w", encoding="utf-8",
                              newline="\n") as fh:
                        fh.write(jraw)
                    st.success("person.json saved")

        cA, cB = st.columns(2)
        if cA.button("Render a take", key="lab-take") and spoken.strip():
            with st.spinner("rendering... the expressive engine takes "
                            "a minute or two"):
                try:
                    st.session_state["lab_take_audio"] = lab_render(
                        spoken, voice_pick, url_pick, lab_opts)
                    st.session_state["lab_take_for"] = (who, which)
                except Exception as e:  # noqa: BLE001
                    st.error(f"render failed: {e}")
        take = st.session_state.get("lab_take_audio")
        if take and st.session_state.get("lab_take_for") == (who, which):
            st.audio(take, format="audio/mp3")
            st.caption("Not right? Adjust the script above and render "
                       "another take -- nothing is saved yet.")
            if cB.button("Attach to profile", key="lab-attach"):
                with open(spath, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(spoken)
                with open(tpath.replace(".txt", ".mp3"), "wb") as fh:
                    fh.write(take)
                st.session_state.pop("lab_take_audio", None)
                st.success(which.replace(".txt", ".mp3")
                           + f" attached to {who} -- live on the site "
                           "after the next Publish")


# ---------------------------------------------------------------------------
#  Who is this? (2026-09-01, Jason's spec): the restored faces enter the
#  Studio as UNKNOWN. One large preview at a time; the vision model can
#  offer a description or a recognition on demand (a SUGGESTION, never an
#  answer -- Jason confirms); the name and notes persist to
#  identities.json, searchable and resumable across sessions.
# ---------------------------------------------------------------------------
with tab_who:
    IDENTS = os.path.join(HERE, "identities.json")
    FACES_DIR = os.path.join(LEGACY, "Photos-1-001 (1)", "AI_Restored_Faces")

    def idents_load():
        try:
            with open(IDENTS, encoding="utf-8") as f:
                return _json.load(f)
        except (OSError, ValueError):
            return {}

    def idents_save(d):
        with open(IDENTS, "w", encoding="utf-8", newline="\n") as f:
            _json.dump(d, f, indent=1, ensure_ascii=False)

    def vision_ask(path):
        """qwen3-vl looks at the photograph. A description and -- only if
        it genuinely recognises a public figure -- a name. Suggestions,
        clearly labelled; Jason's click is what makes anything true."""
        import base64
        b64 = base64.b64encode(Path(path).read_bytes()).decode()
        body = _json.dumps({
            "model": os.environ.get("VISION_MODEL", "gemma4:12b"),
            "stream": False,
            "messages": [{
                "role": "user",
                "content": (
                    "Describe this portrait briefly (era, clothing, "
                    "setting). If -- and ONLY if -- you clearly recognise "
                    "a specific famous person, name them and say why; "
                    "otherwise say the person is unidentified. Two or "
                    "three sentences."),
                "images": [b64],
            }],
        }).encode()
        req = _ur.Request(OLLAMA + "/api/chat", data=body,
                          headers={"Content-Type": "application/json"})
        with _ur.urlopen(_http_only(req), timeout=600) as r:  # nosec B310
            return _json.loads(r.read())["message"]["content"]

    ids = idents_load()
    if not os.path.isdir(FACES_DIR):
        st.error(f"faces folder not found: {FACES_DIR}")
    else:
        faces = sorted(f for f in os.listdir(FACES_DIR)
                       if f.lower().endswith((".jpg", ".jpeg", ".png")))
        known = sum(1 for f in faces if ids.get(f, {}).get("name"))
        st.markdown(f"**{known} identified &middot; "
                    f"{len(faces) - known} still unknown** of {len(faces)}")

        q = st.text_input("Search the identified (name or note)",
                          "", key="who-search")
        if q.strip():
            hits = [(f, v) for f, v in ids.items()
                    if q.lower() in (v.get("name", "") + " "
                                     + v.get("notes", "")).lower()]
            st.caption(f"{len(hits)} match(es)")
            cols = st.columns(4)
            for i, (f, v) in enumerate(hits[:12]):
                with cols[i % 4]:
                    fp = os.path.join(FACES_DIR, f)
                    if os.path.exists(fp):
                        st.image(fp, caption=v.get("name", "?"),
                                 use_container_width=True)

        st.divider()
        mode = st.radio("Queue", ["Next unknown", "Browse everything"],
                        horizontal=True, key="who-mode")
        unknowns = [f for f in faces if not ids.get(f, {}).get("name")]
        pool = unknowns if mode == "Next unknown" else faces
        if not pool:
            st.success("every face has a name. All 1,674.")
        else:
            pos = st.number_input(
                "Photo", 1, len(pool),
                min(st.session_state.get("who-pos", 1), len(pool)),
                key="who-pos")
            fname = pool[pos - 1]
            fpath = os.path.join(FACES_DIR, fname)
            left, right = st.columns([3, 2])
            with left:
                st.image(fpath, use_container_width=True)
                st.caption(fname)
            with right:
                cur = ids.get(fname, {})
                name = st.text_input("Who is this?",
                                     cur.get("name", ""),
                                     key=f"who-name-{fname}")
                notes = st.text_area("Notes (how you know, where, when)",
                                     cur.get("notes", ""), height=90,
                                     key=f"who-notes-{fname}")
                c1, c2 = st.columns(2)
                if c1.button("Save", key=f"who-save-{fname}",
                             type="primary"):
                    ids[fname] = {"name": name.strip(),
                                  "notes": notes.strip()}
                    idents_save(ids)
                    st.success("saved" if name.strip()
                               else "saved as still-unknown")
                    st.rerun()
                if c2.button("Skip \u2192", key=f"who-skip-{fname}"):
                    st.session_state["who-pos"] = (pos % len(pool)) + 1
                    st.rerun()
                if st.button("\U0001F441 Ask the model",
                             key=f"who-ask-{fname}"):
                    with st.spinner("the model is looking..."):
                        try:
                            st.session_state[f"who-sight-{fname}"] = \
                                vision_ask(fpath)
                        except Exception as e:  # noqa: BLE001
                            st.error(f"vision failed: {e}")
                sight = st.session_state.get(f"who-sight-{fname}")
                if sight:
                    st.info("**The model's suggestion** (yours to "
                            "confirm or ignore):\n\n" + sight)


# ---------------------------------------------------------------------------
#  The roster (2026-09-01, Jason's spec): every person on the dial is a
#  folder -- person.json, photos, tellings, narration, handoff clips, and
#  notes.md -- and this tab is the way to see all of them at once, search
#  every word in them, catch duplicates before they happen, start new
#  people, and draw the connections between them.
# ---------------------------------------------------------------------------
with tab_roster:
    def norm_name(n):
        return _re.sub(r"[^a-z0-9]", "", (n or "").lower())

    def roster_read():
        out = []
        for slug in sorted(os.listdir(PEOPLE)):
            d = person_dir(slug)
            pj = os.path.join(d, "person.json")
            if not os.path.isfile(pj):
                continue
            try:
                p = _json.loads(Path(pj).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                p = {}
            files = os.listdir(d)
            photos = [f for f in files
                      if f.lower().endswith((".jpg", ".jpeg", ".png"))]
            stories = [f for f in files
                       if _re.fullmatch(r"story-\d+\.txt", f)]
            narrated = [f for f in files
                        if _re.fullmatch(r"story-\d+\.mp3", f)]
            text = ""
            for f in stories:
                try:
                    text += Path(os.path.join(d, f)).read_text(encoding="utf-8")
                except (OSError, ValueError):
                    pass
            notes = ""
            np = os.path.join(d, "notes.md")
            if os.path.exists(np):
                try:
                    notes = Path(np).read_text(encoding="utf-8")
                except (OSError, ValueError):
                    pass
            out.append({
                "slug": slug, "name": p.get("name", slug), "era": p.get("era", ""),
                "relation": p.get("relation", ""), "draft": p.get("draft", True),
                "enabled": p.get("enabled", True) is not False,
                "photos": len(photos), "stories": len(stories),
                "narrated": len(narrated), "text": text, "notes": notes,
                "thumb": os.path.join(d, photos[0]) if photos else None,
            })
        return out

    roster = roster_read()
    done = sum(1 for r in roster if r["stories"] and r["photos"]
               and not r["draft"])
    st.markdown(f"**{len(roster)} people &middot; {done} finished &middot; "
                f"{sum(r['photos'] for r in roster)} photographs &middot; "
                f"{sum(r['stories'] for r in roster)} tellings**")

    # ---- duplicates, before they multiply ----
    dupes = []
    for i, a in enumerate(roster):
        for b in roster[i + 1:]:
            na, nb = norm_name(a["name"]), norm_name(b["name"])
            if not na or not nb:
                continue
            if na == nb or (len(na) > 5 and len(nb) > 5
                            and (na in nb or nb in na)):
                dupes.append((a, b))
    if dupes:
        st.warning("Possible duplicates: " + " &middot; ".join(
            f"{a['slug']} / {b['slug']}" for a, b in dupes))

    # ---- the search bar over everybody ----
    q = st.text_input("Search everyone -- name, era, relation, the words of "
                      "their tellings, your notes", "", key="ros-q")
    shown = roster
    if q.strip():
        ql = q.lower()
        shown = [r for r in roster if ql in (
            r["name"] + " " + r["slug"] + " " + r["era"] + " " +
            r["relation"] + " " + r["text"] + " " + r["notes"]).lower()]
        st.caption(f"{len(shown)} of {len(roster)} match")

    cols = st.columns(4)
    for i, r in enumerate(shown):
        with cols[i % 4]:
            if r["thumb"]:
                st.image(r["thumb"], use_container_width=True)
            flag = "" if r["enabled"] else " (off the dial)"
            st.markdown(f"**{r['name']}**{flag}")
            st.caption(f"{r['photos']} photos &middot; {r['stories']} "
                       f"tellings &middot; {r['narrated']} narrated"
                       + (" &middot; draft" if r["draft"] else ""))
            st.caption(r["slug"])

    st.divider()
    st.subheader("Start a new person")
    st.caption("Creates people/<slug>/ with person.json and notes.md. "
               "Photos, tellings and narration all live in that folder "
               "from then on -- one person, one place.")
    nc1, nc2 = st.columns([3, 2])
    new_name = nc1.text_input("Name", "", key="ros-new-name")
    new_rel = nc2.text_input("How they connect to Pee-Wee", "",
                             key="ros-new-rel")
    if st.button("Create the folder", key="ros-create") and new_name.strip():
        slug = slugify(new_name)
        clash = [r for r in roster if norm_name(r["name"]) == norm_name(new_name)]
        if clash:
            st.error(f"{clash[0]['name']} is already on the roster "
                     f"({clash[0]['slug']}). Open them on the Work tab "
                     "instead of making a second folder.")
        elif os.path.exists(os.path.join(PEOPLE, slug)):
            st.error(f"people/{slug}/ already exists.")
        else:
            d = person_dir(slug)
            os.makedirs(d)
            with open(os.path.join(d, "person.json"), "w",
                      encoding="utf-8", newline="\n") as f:
                _json.dump({"name": new_name.strip(), "freq": "AM 1600",
                            "era": "", "relation": new_rel.strip(),
                            "caption": "", "bridges": [], "draft": True,
                            "enabled": True}, f, indent=2)
            with open(os.path.join(d, "notes.md"), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write(f"# {new_name.strip()}\n\nSources, family memories, "
                        "and anything still to check.\n")
            st.success(f"people/{slug}/ created -- add photos and tellings "
                       "on the Work tab")
            st.rerun()

    st.divider()
    st.subheader("Connect two people")
    st.caption("A link is spoken by the host ONLY when the dial travels "
               "between these two. House rule: write only what a source or "
               "the family confirms -- no entry, no mention.")
    names = {r["slug"]: r["name"] for r in roster}
    if len(names) >= 2:
        keys = list(names)
        lc1, lc2 = st.columns(2)
        a = lc1.selectbox("Person A", keys,
                          format_func=lambda k: names[k], key="ros-a")
        b = lc2.selectbox("Person B", keys, index=min(1, len(keys) - 1),
                          format_func=lambda k: names[k], key="ros-b")
        cpath = os.path.join(SITE, "data", "connections.json")
        try:
            doc = _json.loads(Path(cpath).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {"links": []}
        existing = next((l for l in doc.get("links", [])
                         if {l.get("a"), l.get("b")} == {a, b}), None)
        text = st.text_area(
            "What connects them -- one spoken sentence",
            (existing or {}).get("text", ""), height=80, key="ros-link-text")
        src = st.text_input("Source (where this is written down)",
                            (existing or {}).get("source", ""),
                            key="ros-link-src")
        if st.button("Save the connection", key="ros-link-save"):
            if a == b:
                st.error("Pick two different people.")
            elif not text.strip():
                st.error("Write the sentence the host should say.")
            else:
                doc.setdefault("links", [])
                if existing:
                    existing["text"] = text.strip()
                    existing["source"] = src.strip()
                    existing.pop("audio", None)   # re-render with new words
                else:
                    doc["links"].append({"a": a, "b": b,
                                         "text": text.strip(),
                                         "source": src.strip()})
                with open(cpath, "w", encoding="utf-8", newline="\n") as f:
                    _json.dump(doc, f, indent=2, ensure_ascii=False)
                st.success("saved -- run connections.py (or Publish) to "
                           "give it a voice")
        if doc.get("links"):
            st.caption(f"{len(doc['links'])} connection(s) on record")
            for l in doc["links"]:
                st.markdown(f"- **{names.get(l['a'], l['a'])} &harr; "
                            f"{names.get(l['b'], l['b'])}** &mdash; "
                            f"{l.get('text', '')}"
                            + (f"  \n  _{l['source']}_" if l.get("source") else ""))
