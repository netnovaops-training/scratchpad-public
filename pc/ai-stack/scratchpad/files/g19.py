import io
def edit(p, pairs):
    s = io.open(p, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) == 1, (p, a[:70], s.count(a2))
        s = s.replace(a2, b2)
    io.open(p, "w", encoding="utf-8", newline="").write(s)

EN_MORE = """- **Command:** `bash garden.sh tailscale`

### 18) Rename or move a file or folder (every place that uses it follows)
- **What it does:** Four choices. `1` renames a file or folder of the website (a photo folder, a page, a data file) and changes every place that uses the name: the code, the pages, the photo lists and the database rows. `2` moves one of the big folders to another place or disk (the same as Settings 8 used to). `3` moves the WHOLE garden folder. `4` lists what was renamed or moved so far. It shows every place first, offers a backup, and changes nothing until you type `y`.
- **When to use it:** To give the website your own names, or to move the garden to a bigger disk.
- **What it installs or changes:** The name, every place that uses it, `IMAGE_FOLDERS` in `.env` for a renamed photo folder, one git entry, and a line in `logs/renames.tsv`. Copies of everything it changes go to `logs/rename_DATE/` first.
- **To undo it:** Choose it again and rename it back.
- **Good to know:** The guides keep showing the names the garden came with.
- **Command:** `bash garden.sh rename`. Guide: `26-rename-move-and-switch.md`.

### 19) On / off switch, check-up and Docker clean-up
- **What it does:** `1` switches everything off (`docker compose down`, without `-v`: nothing is deleted) and stops the front door. `2` switches it all on again, even months later, and checks the website. `3` takes it off the internet only. `4` starts again a program that is stuck. `5` removes Docker's leftovers (old versions of the garden's programs, old build notes). `6` runs the check-up every 6 hours.
- **When to use it:** When you do not want to host the site for a while, when the traffic is too much, or when the disk fills up.
- **What it installs or changes:** Off writes `.garden-off` (the nightly backup rests while it is there). The automatic check-up is one cron line.
- **To undo it:** Off: choose `2`. The automatic check-up: choose `6` again.
- **Command:** `bash garden.sh power`, or straight away `bash garden.sh off`, `bash garden.sh on`, `bash garden.sh checkup`, `bash garden.sh cleanup`. Guide: `26-rename-move-and-switch.md`.
"""
KO_MORE = """- **명령:** `bash garden.sh tailscale`

### 18) Rename or move a file or folder (every place that uses it follows) — 파일이나 폴더 이름 바꾸기 또는 옮기기(그것을 쓰는 모든 곳이 따라와요)
- **무엇을 하나요:** 네 가지 선택. `1`은 웹사이트의 파일이나 폴더(사진 폴더, 페이지, 데이터 파일)의 이름을 바꾸고, 그 이름을 쓰는 모든 곳을 바꿔요: 코드, 페이지, 사진 목록, 데이터베이스 줄. `2`는 큰 폴더 하나를 다른 곳이나 다른 디스크로 옮겨요(예전 설정 8과 같아요). `3`은 정원 폴더 전체를 옮겨요. `4`는 지금까지 바꾸거나 옮긴 것을 보여 줘요. 먼저 모든 곳을 보여 주고, 백업을 권하고, 내가 `y`를 입력하기 전에는 아무것도 바꾸지 않아요.
- **언제 쓰나요:** 웹사이트에 내 이름을 붙이고 싶을 때, 또는 정원을 더 큰 디스크로 옮기고 싶을 때.
- **무엇을 설치하거나 바꾸나요:** 그 이름, 그것을 쓰는 모든 곳, 사진 폴더 이름을 바꾸면 `.env`의 `IMAGE_FOLDERS`, git 기록 한 줄, `logs/renames.tsv`의 한 줄. 바꾸는 것의 사본은 먼저 `logs/rename_DATE/`에 남겨요.
- **되돌리려면:** 다시 골라서 원래 이름으로 바꾸세요.
- **알아 두기:** 안내서에는 계속 정원이 처음 가진 이름이 나와요.
- **명령:** `bash garden.sh rename`. 안내서: [`26-rename-move-and-switch.md`](26-rename-move-and-switch.md).

### 19) On / off switch, check-up and Docker clean-up — 켜기 / 끄기 스위치, 점검, Docker 정리
- **무엇을 하나요:** `1`은 모두 꺼요(`docker compose down`, `-v` 없이: 아무것도 지우지 않아요). 현관문도 멈춰요. `2`는 몇 달 뒤에도 모두 다시 켜고 웹사이트를 확인해요. `3`은 인터넷에서만 내려요. `4`는 멈춘 프로그램을 다시 시작해요. `5`는 Docker의 남은 것(정원 프로그램의 옛 버전, 오래된 빌드 기록)을 지워요. `6`은 6시간마다 점검을 해요.
- **언제 쓰나요:** 한동안 사이트를 운영하고 싶지 않을 때, 접속이 너무 많을 때, 디스크가 찰 때.
- **무엇을 설치하거나 바꾸나요:** 끄기는 `.garden-off`를 만들어요(그 파일이 있는 동안 밤 백업도 쉬어요). 자동 점검은 cron 한 줄이에요.
- **되돌리려면:** 끄기는 `2`를 고르세요. 자동 점검은 `6`을 다시 고르세요.
- **명령:** `bash garden.sh power`, 또는 바로 `bash garden.sh off`, `bash garden.sh on`, `bash garden.sh checkup`, `bash garden.sh cleanup`. 안내서: [`26-rename-move-and-switch.md`](26-rename-move-and-switch.md).
"""

edit("19-every-menu-option.md", [
("- **Command:** `bash garden.sh tailscale`\n", EN_MORE),
("""### 8) Move or rename a folder
- **What it does:** Lists the 8 garden folders (website files, photos, data, cache, database, n8n, sign-in page, backups). You pick one and type its new place. It stops the garden, moves the folder, updates `.env` and starts again.
- **When to use it:** If you want a folder somewhere else or under another name.
- **What it installs or changes:** The folder's place and its line in `.env`. Moving the backups folder also rewrites the nightly backup's cron line.
- **To undo it:** Choose 8 again and move it back.
- Guide: `13-backups-and-folders.md`.
""", """### 8) Rename or move a file or folder
- **What it does:** The same as More 18 (four choices). Choice `2` lists the 8 garden folders (website files, photos, data, cache, database, n8n, sign-in page, backups). You pick one and type its new place, inside the garden folder or on another Linux disk. It stops the garden, moves the folder, updates `.env` and starts again.
- **When to use it:** If you want a folder somewhere else, or a file or folder of the website under your own name (choice `1`).
- **What it installs or changes:** The folder's place and its line in `.env`. Moving the backups folder also rewrites the nightly backup's cron line.
- **To undo it:** Choose 8 again and move it back.
- Guides: `13-backups-and-folders.md`, `26-rename-move-and-switch.md`.
"""),
("""| 19 | Reach the garden from anywhere, privately (Tailscale): on / off | 17 |
""", """| 19 | Reach the garden from anywhere, privately (Tailscale): on / off | 17 |
| 20 | Rename or move a file or folder | 18 |
| 21 | On / off switch, check-up and Docker clean-up | 19 |
"""),
("""- **Guide:** `20-ssh-and-remote-desktop.md`

---

## Every command word""", """- **Guide:** `20-ssh-and-remote-desktop.md`

### More 20) Rename or move a file or folder
- **What it does:** Runs the rename tool on the garden computer (Ubuntu More 18), then makes this PC's copy of the garden folder match: it copies the changed files here and renames the same site files, so a later INSTALL from this PC does not bring the old names back.
- **What it installs or changes:** The same files on both computers. This PC's old files go to `logs\\rename-windows_DATE\\`.
- **To undo it:** Run it again with the names swapped.

### More 21) On / off switch, check-up and Docker clean-up
- **What it does:** The same as Ubuntu More 19, on the garden computer.

---

## Every command word"""),
("""| `webname` | More 16 | | |
""", """| `webname` | More 16 | `tailscale` | More 17 |
| `rename` | More 18 | `power` | More 19 |
| `off` / `on` | More 19, 1 / 2 | `checkup` / `cleanup` | More 19, 4 / 5 |
"""),
("""- `bash garden.sh dbweb-stop`: stops and removes Adminer.
""", """- `bash garden.sh dbweb-stop`: stops and removes Adminer.
- `bash garden.sh renames`: lists what was renamed or moved (`logs/renames.tsv`).
- `bash garden.sh redo-renames`: after restoring an old backup by hand, does its renames again.
"""),
])

edit("ko/19-every-menu-option.md", [
("- **명령:** `bash garden.sh tailscale`\n", KO_MORE),
("""### 8) Move or rename a folder — 폴더 옮기기 또는 이름 바꾸기
- **무엇을 하나요:** 정원 폴더 8개(웹사이트 파일, 사진, 데이터, 캐시, 데이터베이스, n8n, 로그인 페이지, 백업)를 보여 줘요. 하나를 고르고 새 위치를 입력하세요. 정원을 멈추고, 폴더를 옮기고, `.env`를 업데이트하고, 다시 시작해요.
- **언제 쓰나요:** 폴더를 다른 곳에 두거나 다른 이름으로 바꾸고 싶을 때.
- **무엇을 설치하거나 바꾸나요:** 폴더의 위치와 `.env`의 그 줄. 백업 폴더를 옮기면 매일 밤 백업의 cron 줄도 다시 써요.
- **되돌리려면:** 8을 다시 고르고 원래 자리로 옮기세요.
- **안내서:** [`13-backups-and-folders.md`](13-backups-and-folders.md).
""", """### 8) Rename or move a file or folder — 파일이나 폴더 이름 바꾸기 또는 옮기기
- **무엇을 하나요:** 더 보기 18과 같아요(네 가지 선택). `2`를 고르면 정원 폴더 8개(웹사이트 파일, 사진, 데이터, 캐시, 데이터베이스, n8n, 로그인 페이지, 백업)를 보여 줘요. 하나를 고르고 새 위치를 입력하세요. 정원 폴더 안이나 다른 Linux 디스크도 돼요. 정원을 멈추고, 폴더를 옮기고, `.env`를 업데이트하고, 다시 시작해요.
- **언제 쓰나요:** 폴더를 다른 곳에 두고 싶을 때, 또는 웹사이트의 파일이나 폴더에 내 이름을 붙이고 싶을 때(`1`).
- **무엇을 설치하거나 바꾸나요:** 폴더의 위치와 `.env`의 그 줄. 백업 폴더를 옮기면 매일 밤 백업의 cron 줄도 다시 써요.
- **되돌리려면:** 8을 다시 고르고 원래 자리로 옮기세요.
- **안내서:** [`13-backups-and-folders.md`](13-backups-and-folders.md), [`26-rename-move-and-switch.md`](26-rename-move-and-switch.md).
"""),
("""| 19 | Reach the garden from anywhere, privately (Tailscale): on / off(어디서나 나만 정원에 닿기(Tailscale): 켜기 / 끄기) | 17 |
""", """| 19 | Reach the garden from anywhere, privately (Tailscale): on / off(어디서나 나만 정원에 닿기(Tailscale): 켜기 / 끄기) | 17 |
| 20 | Rename or move a file or folder(파일이나 폴더 이름 바꾸기 또는 옮기기) | 18 |
| 21 | On / off switch, check-up and Docker clean-up(켜기 / 끄기 스위치, 점검, Docker 정리) | 19 |
"""),
("""- **안내서:** [20-ssh-and-remote-desktop.md](20-ssh-and-remote-desktop.md)

---
""", """- **안내서:** [20-ssh-and-remote-desktop.md](20-ssh-and-remote-desktop.md)

### More 20) Rename or move a file or folder — 파일이나 폴더 이름 바꾸기 또는 옮기기
- **무엇을 하나요:** 정원 컴퓨터에서 이름 바꾸기 도구를 실행하고(Ubuntu 더 보기 18), 이 PC에 있는 정원 폴더 사본도 똑같이 맞춰요: 바뀐 파일을 여기로 복사하고 같은 사이트 파일의 이름을 바꿔요. 그래서 나중에 이 PC에서 INSTALL을 해도 옛 이름이 돌아오지 않아요.
- **무엇을 설치하거나 바꾸나요:** 두 컴퓨터의 같은 파일. 이 PC의 옛 파일은 `logs\\rename-windows_DATE\\`에 남아요.
- **되돌리려면:** 이름을 바꿔서 다시 실행하세요.

### More 21) On / off switch, check-up and Docker clean-up — 켜기 / 끄기 스위치, 점검, Docker 정리
- **무엇을 하나요:** 정원 컴퓨터에서 Ubuntu 더 보기 19와 같은 일을 해요.

---
"""),
("""| `webname` | More 16 | | |
""", """| `webname` | More 16 | `tailscale` | More 17 |
| `rename` | More 18 | `power` | More 19 |
| `off` / `on` | More 19, 1 / 2 | `checkup` / `cleanup` | More 19, 4 / 5 |
"""),
("""- `bash garden.sh dbweb-stop`: Adminer를 멈추고 지워요.
""", """- `bash garden.sh dbweb-stop`: Adminer를 멈추고 지워요.
- `bash garden.sh renames`: 바꾸거나 옮긴 것의 목록(`logs/renames.tsv`).
- `bash garden.sh redo-renames`: 옛 백업을 손으로 복원한 뒤, 그 이름 바꾸기를 다시 해요.
"""),
])
print("ok")
