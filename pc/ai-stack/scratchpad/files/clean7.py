from pathlib import Path


def edit(p, pairs):
    s = Path(p).read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) == 1, (p, a[:80], s.count(a2))
        s = s.replace(a2, b2)
    Path(p).write_text(s, encoding="utf-8", newline="")

OLD_WHAT = "Removes old versions of the garden's programs (left behind each time one is rebuilt or updated) and Docker's build notes older than 30 days. It keeps every image the garden uses, also those of parts that are off right now. Images of your other Docker projects are left alone."
NEW_WHAT = "Removes old versions of the garden's programs (left behind each time one is rebuilt or updated) that are older than 7 days, and Docker's build notes older than 30 days. It keeps every image the garden uses, also those of parts that are off right now, and anything newer than 7 days (after you edited files and rebuilt, the old version stays in case you need to go back). Images of your other Docker projects are left alone. Use it with care: it shows the list and asks first, and Enter means no."
edit("garden.sh", [
(OLD_WHAT, NEW_WHAT),
("""    local keep keep_names id repo p
    local -a all_profiles=() remove=()""", """    local keep keep_names id repo p made young=0
    local -a all_profiles=() remove=()"""),
("""        [ -n "$(dk ps -a -q --filter ancestor="$id")" ] && continue
        remove+=(""", """        [ -n "$(dk ps -a -q --filter ancestor="$id")" ] && continue
        # Newer than 7 days stays: a recent rebuild's old version may still be wanted.
        made=$(date -d "$(dk image inspect -f '{{.Created}}' "$id" 2>/dev/null)" +%s 2>/dev/null || date +%s)
        if [ $(( $(date +%s) - made )) -lt $((7 * 86400)) ]; then young=$((young + 1)); continue; fi
        remove+=("""),
("""        if confirm "Remove these?" y; then""", """        if confirm "Remove these?" n; then"""),
("""    else
        note "No unused images."
    fi
    note "Old build notes (older than 30 days):\"""", """    else
        note "No unused images older than 7 days."
    fi
    [ "$young" -gt 0 ] && note "Kept $young newer than 7 days (they go in a later clean-up)."
    note "Old build notes (older than 30 days):\""""),
])
edit("lang/ko.tsv", [
(OLD_WHAT + "\t", NEW_WHAT + "\t"),
("No unused images.\t쓰지 않는 이미지가 없어요.", "No unused images older than 7 days.\t7일이 넘은, 쓰지 않는 이미지가 없어요."),
])
# The Korean of the new "What" text.
s = Path("lang/ko.tsv").read_bytes().decode("utf-8")
nl = "\r\n" if "\r\n" in s else "\n"
lines = s.split(nl)
for i, l in enumerate(lines):
    if l.startswith(NEW_WHAT + "\t"):
        lines[i] = NEW_WHAT + "\t" + ("정원 프로그램의 옛 버전(다시 만들거나 업데이트할 때마다 남는 것) 중 7일이 넘은 것과, 30일 넘은 Docker 빌드 기록을 지워요. "
                                      "정원이 쓰는 이미지는 지금 꺼져 있는 부분 것까지 모두 남기고, 7일이 안 된 것도 남겨요(파일을 고치고 다시 만든 뒤, 되돌아가야 할 때를 위해 옛 버전이 남아 있어요). "
                                      "다른 Docker 프로젝트의 이미지는 건드리지 않아요. 조심해서 쓰세요: 먼저 목록을 보여 주고 물어보며, Enter는 '아니요'예요.")
lines.append("Kept $young newer than 7 days (they go in a later clean-up).\t7일이 안 된 $young개는 남겼어요(나중 정리 때 지워요).")
Path("lang/ko.tsv").write_text(nl.join(l for l in lines if l is not None), encoding="utf-8", newline="")
print("ok")
