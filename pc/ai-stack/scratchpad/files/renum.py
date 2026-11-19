import re
from pathlib import Path


def edit(p, pairs):
    s = Path(p).read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) >= 1, (p, a[:80])
        s = s.replace(a2, b2)
    Path(p).write_text(s, encoding="utf-8", newline="")

def cut_section(p, start, until):
    """Remove from the line starting with `start` up to (not including) the next line starting with `until`."""
    s = Path(p).read_bytes().decode("utf-8")
    i = s.index(start); j = s.index(until, i)
    Path(p).write_text(s[:i] + s[j:], encoding="utf-8", newline="")

# ---------- guide 19, Korean ----------
K = "ko/19-every-menu-option.md"
s = Path(K).read_text(encoding="utf-8")
m = re.search(r"- \*\*되돌리려면:\*\* More, 14[^\n]*", s)

edit(K, [
("""### 14) Turn the front door off (take the garden off the internet) — 정문 끄기(정원을 인터넷에서 내리기)
- **무엇을 하나요:** Caddy를 멈추고,""", """### 14) On / off: the whole garden, or only off the internet; check-up, clean-up — 켜기 / 끄기: 정원 전체, 또는 인터넷에서만 내리기; 점검, 정리
- **무엇을 하나요:** 여섯 가지 선택이 있는 화면 하나예요. `1`은 모두 꺼요(`docker compose down`, `-v` 없이: 아무것도 지우지 않아요). 현관문도 멈춰요. `2`는 몇 달 뒤에도 모두 다시 켜고 웹사이트를 확인해요. `4`는 멈춘 프로그램을 다시 시작해요. `5`는 Docker의 남은 것(정원 프로그램의 옛 버전, 오래된 빌드 기록)을 지워요. `6`은 6시간마다 점검을 해요.
- **`3`, 인터넷에서만 내리기:** Caddy를 멈추고,"""),
("""- **언제 쓰나요:** 정원을 잠시 인터넷에서 내리고 싶을 때, 또는 옮기기 전에.
- **무엇을 설치하거나 바꾸나요:** Caddy와 No-IP 업데이트 프로그램은 꺼져요(지워지지는 않아요). DuckDNS cron 줄은 지워져요. `.env`: 웹 이름이 비워지고, 주소가 localhost로 돌아가요.
- **되돌리려면:** More, 1을 다시 하세요(No-IP 업데이트 프로그램을 다시 켜요). 하위 도메인이 있었다면 Subdomains, 1도 하세요(DuckDNS 업데이트 프로그램을 다시 추가해요). 완전히 닫고 싶다면, 공유기에서 80과 443 전달도 직접 지우세요.
- **명령:** `bash garden.sh offline`""", """- **언제 쓰나요:** `1`은 한동안 사이트를 운영하고 싶지 않을 때나 접속이 너무 많을 때. `3`은 인터넷에서는 내리고 집에서는 쓰고 싶을 때, 또는 옮기기 전에. `5`는 디스크가 찰 때.
- **무엇을 설치하거나 바꾸나요:** `1`은 `.garden-off`를 만들어요(그 파일이 있는 동안 밤 백업도 쉬어요). `3`: Caddy와 No-IP 업데이트 프로그램은 꺼져요(지워지지는 않아요). DuckDNS cron 줄은 지워지고, `.env`의 웹 이름이 비워지고 주소가 localhost로 돌아가요. `6`은 cron 한 줄이에요.
- **되돌리려면:** `1`: `2`를 고르세요. `3`: More, 1을 다시 하세요(No-IP 업데이트 프로그램을 다시 켜요). 하위 도메인이 있었다면 Subdomains, 1도 하세요. 완전히 닫고 싶다면 공유기에서 80과 443 전달도 직접 지우세요. `6`: 다시 고르세요.
- **명령:** `bash garden.sh power`, 또는 바로 `bash garden.sh off`, `bash garden.sh on`, `bash garden.sh offline`(`3`), `bash garden.sh checkup`, `bash garden.sh cleanup`. 안내서: [`26-rename-move-and-switch.md`](26-rename-move-and-switch.md)."""),
("| 15 | Turn the front door off(정문 끄기) | 14 |", "| 15 | On / off: the whole garden, or only off the internet; check-up, clean-up(켜기 / 끄기, 점검, 정리) | 14 |"),
("| 21 | On / off switch, check-up and Docker clean-up(켜기 / 끄기 스위치, 점검, Docker 정리) | 19 |\n| 22 | Find the garden computer again(정원 컴퓨터 다시 찾기) | (Windows만) |",
 "| 21 | Find the garden computer again(정원 컴퓨터 다시 찾기) | (Windows만) |"),
("""### More 21) On / off switch, check-up and Docker clean-up — 켜기 / 끄기 스위치, 점검, Docker 정리
- **무엇을 하나요:** 정원 컴퓨터에서 Ubuntu 더 보기 19와 같은 일을 해요.

### More 22) Find""", """### More 21) Find"""),
("| `offline` | More 14 | `llm` | More 15 |", "| `offline` | More 14, 3 | `llm` | More 15 |"),
("| `rename` | More 18 | `power` | More 19 |\n| `off` / `on` | More 19, 1 / 2 | `checkup` / `cleanup` | More 19, 4 / 5 |",
 "| `rename` | More 18 | `power` | More 14 |\n| `off` / `on` | More 14, 1 / 2 | `checkup` / `cleanup` | More 14, 4 / 5 |\n| `renames` | More 18, 4 | | |"),
("- `bash garden.sh renames`: 바꾸거나 옮긴 것의 목록(`logs/renames.tsv`).\n", ""),
("| 집 Wi-Fi, 연습, 또는 온라인(정문) | More 1 | More 14 |", "| 집 Wi-Fi, 연습, 또는 온라인(정문) | More 1 | More 14, 3 |"),
("Subdomains 3. More 14는 업데이트 프로그램을 멈춰요 |", "Subdomains 3. More 14, 3은 업데이트 프로그램을 멈춰요 |"),
])
cut_section(K, "### 19) On / off switch, check-up and Docker clean-up", "## Backups (6)")
s = Path(K).read_bytes().decode("utf-8")
i = s.index("`garden.sh`가 보여 주는 메시지는 Ubuntu 번호를 써요.")
j = s.index("\n\n", i)
nl = "\r\n" if "\r\n" in s else "\n"
s = s[:i] + ("`garden.sh`가 보여 주는 메시지는 Ubuntu 번호를 써요. Windows에서는 Ubuntu 13~16은" + nl +
             "1을 더하고(Ubuntu의 \"menu M, then 13\"은 Windows의 More 14예요), Ubuntu 17과 18은 2를" + nl +
             "더하세요(Tailscale은 Windows More 19, 이름 바꾸기는 Windows More 20이에요).") + s[j:]
Path(K).write_text(s, encoding="utf-8", newline="")

# ---------- the other guides and pages ----------
for p, pairs in {
    "26-rename-move-and-switch.md": [("menu `M`, then `19`. **Off**", "menu `M`, then `14`. **Off**"),
        ("`M`, then `20` (rename) and `21` (switch).", "`M`, then `20` (rename) and `15` (switch)."),
        ("Menu `M`, then `19`.\n", "Menu `M`, then `14`.\n"),
        ("the garden keeps running at home (the same as More `14`).", "the garden keeps running at home; menu `M`, `1` puts it back online.")],
    "ko/26-rename-move-and-switch.md": [("메뉴 `M`, 그다음 `19`. **끄기**", "메뉴 `M`, 그다음 `14`. **끄기**"),
        ("`M`, 그다음 `20`(이름 바꾸기)과 `21`(스위치).", "`M`, 그다음 `20`(이름 바꾸기)과 `15`(스위치)."),
        ("메뉴 `M`, 그다음 `19`.\n", "메뉴 `M`, 그다음 `14`.\n"),
        ("집에서는 정원이 계속 돌아가요(더 보기 `14`와 같아요).", "집에서는 정원이 계속 돌아가요. 메뉴 `M`, `1`로 다시 인터넷에 올려요.")],
    "25-common-questions.md": [("Menu `M`, then `19`, then `1`", "Menu `M`, then `14`, then `1`"), ("Menu `M`, `19`, then `2`", "Menu `M`, `14`, then `2`"),
        ("**Keys:** menu `M`, then `19`", "**Keys:** menu `M`, then `14`"), ("menu `M`, `19`, then `5`", "menu `M`, `14`, then `5`"),
        ("**Keys:** menu `5`, and menu `M`, then `19`", "**Keys:** menu `5`, and menu `M`, then `14`")],
    "ko/25-common-questions.md": [("메뉴 `M`, 그다음 `19`, 그다음 `1`", "메뉴 `M`, 그다음 `14`, 그다음 `1`"), ("메뉴 `M`, `19`,\n그다음 `2`", "메뉴 `M`, `14`,\n그다음 `2`"),
        ("**누를 키:** 메뉴 `M`, 그다음 `19`", "**누를 키:** 메뉴 `M`, 그다음 `14`"), ("메뉴 `M`, `19`,\n그다음 `5`", "메뉴 `M`, `14`,\n그다음 `5`"),
        ("**누를 키:** 메뉴 `5`, 그리고 메뉴 `M`, 그다음 `19`", "**누를 키:** 메뉴 `5`, 그리고 메뉴 `M`, 그다음 `14`")],
    "14-reserve-your-ip.md": [("(More `22`)", "(More `21`)"), ("Windows menu, `M`, then `22`", "Windows menu, `M`, then `21`")],
    "ko/14-reserve-your-ip.md": [("(더 보기 `22`)", "(더 보기 `21`)"), ("Windows 메뉴, `M`, 그다음 `22`", "Windows 메뉴, `M`, 그다음 `21`")],
    "04-go-public-no-ip-caddy.md": [("Menu `M`, then `14`: stops Caddy", "Menu `M`, then `14`, then `3` (Off the internet only): stops Caddy")],
    "ko/04-go-public-no-ip-caddy.md": [("메뉴 `M`, 그다음 `14`: Caddy를", "메뉴 `M`, 그다음 `14`, 그다음 `3`(인터넷에서만 내리기): Caddy를")],
}.items():
    edit(p, pairs)
print("ok")
