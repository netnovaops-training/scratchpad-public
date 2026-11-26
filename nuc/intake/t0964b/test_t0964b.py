"""test_t0964b.py - proof for the T0964 classify tidy-ups. Run INSIDE the patched intake image (model faked):

    docker run --rm -v DROP.toml:/config/drop.toml:ro -e PENDING_ROOT=/srv/unsorted/.pending \\
        --entrypoint python IMAGE test_t0964b.py

Checks: a .docx and an .odt give their text and reach the model; a broken .docx and a zip bomb give "" (filed
as before, never a crash); .csv is read; a document sent elsewhere by drop.toml says where; tabs survive
quote_untrusted as spaces; portal's MAX_GB shows one decimal and survives a non-number setting.
Variables: tmp (throw-away folder), fails (FAIL count), sent (what the fake model was given).
"""
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import classify

fails = 0
sent = []


def check(name, ok):
    global fails
    fails += not ok
    print(("PASS " if ok else "FAIL ") + name)


def fake(answer):
    def model(filename, txt):
        sent.append(txt)
        return answer
    return model


tmp = Path(tempfile.mkdtemp())
docx = tmp / "letter.docx"
with zipfile.ZipFile(docx, "w") as z:
    z.writestr("word/document.xml", '<w:document><w:body><w:p><w:r><w:t>Electric bill</w:t></w:r></w:p>'
                                    '<w:p><w:r><w:t>Amount due 42.10</w:t></w:r></w:p></w:body></w:document>')
odt = tmp / "recipe.odt"
with zipfile.ZipFile(odt, "w") as z:
    z.writestr("content.xml", '<office:document-content><text:h>Pancakes</text:h>'
                              '<text:p>2 eggs, 1 cup flour, mix and fry</text:p></office:document-content>')
broken = tmp / "broken.docx"
broken.write_bytes(b"this is not a zip file at all")
bomb = tmp / "bomb.docx"
with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as z:
    z.writestr("word/document.xml", "<w:p>" + "a" * (classify.XML_MAX + 10) + "</w:p>")
csv = tmp / "budget.csv"
csv.write_text("item,amount\nrent,1200\n")

t = classify.extract_text(str(docx), ".docx")
check(f"docx text read ({t!r})", "Electric bill" in t and "Amount due 42.10" in t and "<" not in t)
t = classify.extract_text(str(odt), ".odt")
check(f"odt text read ({t[:40]!r})", "Pancakes" in t and "2 eggs" in t)
check("broken docx -> no text, no crash", classify.extract_text(str(broken), ".docx") == "")
check("zip bomb docx -> no text (size cap)", classify.extract_text(str(bomb), ".docx") == "")

classify.ask_model = fake("document")
v = classify.classify(str(docx), "letter.docx", None, None)
check("docx reaches the model -> document", v["kind"] == "document" and "Electric bill" in sent[-1]
      and "model read" in v["why"])
n = len(sent)
v = classify.classify(str(csv), "budget.csv", None, None)
check(".csv is read and sent to the model", len(sent) == n + 1 and "rent,1200" in sent[-1])
check("document to Paperless keeps its note", v["dest"] == "/srv/consume" and "Paperless decides" in v["why"])

saved = classify.LIBRARIES
classify.LIBRARIES = {**saved, "document": {"dest": "/srv/paperwork", "grant": "paperless"}}
v = classify.classify(str(docx), "letter.docx", None, None)
check("document sent elsewhere says where", v["dest"] == "/srv/paperwork" and "(/srv/paperwork)" in v["why"])
classify.LIBRARIES = saved

check("quote_untrusted keeps tabs as spaces", classify.quote_untrusted("rent\t1200\nfood\t300") == "rent 1200\nfood 300")

for env, want in (("10240", "10"), ("1536", "1.5"), ("1500", "1.5"), ("lots", "2")):
    r = subprocess.run([sys.executable, "-c", "import portal; print(portal.MAX_GB)"], capture_output=True, text=True,
                       env={**os.environ, "INTAKE_MAX_MB": env}, timeout=60)
    check(f"MAX_GB for INTAKE_MAX_MB={env} -> {want}", r.returncode == 0 and r.stdout.strip() == want)

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
