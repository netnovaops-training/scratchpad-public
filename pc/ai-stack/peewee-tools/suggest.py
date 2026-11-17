#!/usr/bin/env python3
# =============================================================================
#  suggest.py -- first pass over the Legacy photos: what might each one show?
#
#  These are phone photos of PHYSICAL photographs from Peewee Harris's own
#  collection -- press shots, publicity photos, pictures taken with the
#  people who came through Albany radio. They have been AI-enhanced and
#  cropped over the years, so this pass is a STARTING POINT, never a verdict:
#  the model describes each photo and, where it believes the subject is a
#  widely known public figure, offers a name and says why it thinks so.
#
#  EVERYTHING IT WRITES IS MARKED unverified. A person confirms or corrects
#  each entry in data/review.json before anything appears on the site or is
#  spoken on the phone line. The machine drafts; the family decides. That is
#  not a disclaimer, it is the design: this site is a tribute Jason's aunt
#  will hear read aloud, and a wrong name spoken in a voice she trusts is
#  worse than a blank one.
# =============================================================================
import base64, json, os, sys, time, urllib.request

OLLAMA = "http://127.0.0.1:11434"
# Consolidated onto the one generalist 2026-09-13. Hardcoded rather than
# env-driven, which is why the repointing sweep had to grep for it.
MODEL = "gemma4:12b"
PHOTOS = "peewee/site/photos"
OUT = "peewee/data/review.json"

PROMPT = (
    "This is a digitised photograph from the personal collection of a radio "
    "disc jockey who worked in Albany, New York from the 1950s onward. His "
    "collection mixes family pictures, station publicity shots, and photos "
    "with entertainers, athletes and public figures who passed through.\n\n"
    "Describe what the photograph shows: how many people, the era suggested "
    "by dress and film stock, the setting, whether it reads as a posed "
    "publicity photo, a candid, or a family picture.\n\n"
    "If and only if a person in it appears to be a widely known public "
    "figure, give the name you believe it is and say what visual evidence "
    "makes you think so. If you are not confident, say so plainly -- an "
    "honest 'unidentified' is more useful than a guess.\n\n"
    "Answer as JSON: {\"description\": str, \"era_guess\": str, "
    "\"kind\": \"publicity|candid|family|document|unclear\", "
    "\"possible_public_figures\": [{\"name\": str, \"confidence\": "
    "\"high|medium|low\", \"why\": str}]}"
)


def look(path):
    img = base64.b64encode(open(path, "rb").read()).decode()
    body = json.dumps({
        "model": MODEL, "stream": False, "options": {"temperature": 0},
        "keep_alive": "2m",
        "messages": [{"role": "user", "content": PROMPT, "images": [img]}],
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        text = json.loads(r.read())["message"]["content"]
    s, e = text.find("{"), text.rfind("}")
    return json.loads(text[s:e + 1]) if s > -1 else {"description": text}


def main():
    existing = {}
    if os.path.exists(OUT):
        existing = {p["photo"]: p for p in json.load(open(OUT))}
    out = []
    files = sorted(f for f in os.listdir(PHOTOS) if f.endswith(".jpg"))
    for i, f in enumerate(files, 1):
        if f in existing and existing[f].get("status") != "error":
            out.append(existing[f])
            continue
        print(f"[{i}/{len(files)}] {f}", flush=True)
        try:
            d = look(os.path.join(PHOTOS, f))
            out.append({"photo": f, "status": "unverified", **d,
                        "confirmed_people": [],   # <- the family fills this in
                        "note": ""})
        except Exception as e:
            print(f"  failed: {e}", flush=True)
            out.append({"photo": f, "status": "error", "error": str(e)})
        json.dump(out, open(OUT, "w"), indent=1)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
