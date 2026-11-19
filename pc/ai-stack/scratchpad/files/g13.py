from pathlib import Path


def edit(p, pairs):
    s = Path(p).read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) == 1, (p, a[:60])
        s = s.replace(a2, b2)
    Path(p).write_text(s, encoding="utf-8", newline="")

edit("13-backups-and-folders.md", [
("""- To move or rename one of the garden's folders (photos, backups and so on), use menu `8`, then `8`.
- Never rename the folders inside your photos folder, or photo files, by hand.
""",
"""- To rename or move a file or folder (photos, pages, backups and so on), use menu `M`, then `18` (or menu `8`, then `8`). Every place that uses it changes with it: guide 26.
- Do not rename the folders inside your photos folder, or photo files, by hand: use that menu, so the website and the database follow.
"""),
("""Every folder the garden uses is set in ONE place, and **Settings** changes it for
you: menu `8`, then `8` (Move or rename a folder). It stops the garden, moves the
folder, saves the new name, and starts again. The checklist then confirms every
folder is found.
""",
"""Every folder the garden uses is set in ONE place, and **Settings** changes it for
you: menu `8`, then `8` (Rename or move a file or folder), then `2`. It stops the
garden, moves the folder (to another disk too), saves the new place, and starts
again. The checklist then confirms every folder is found. Guide 26 shows every step.
"""),
("""**Never rename these by hand:**
- **The folders INSIDE your photos folder** (`Database`, `Gallery`, `post`, `blog`,
  `plan`, `HeroBanner`). The website looks for exactly those names.
- **Photo file names.** The database remembers each photo by its name.
  Rename one and the page shows a broken picture. Upload a new photo through the
  website instead.
""",
"""**Do not rename these by hand** (the website would lose them):
- **The folders INSIDE your photos folder** (`Database`, `Gallery`, `post`, `blog`,
  `plan`, `HeroBanner`). The website looks for exactly those names.
- **Photo file names.** The database remembers each photo by its name.

To give them your own names, use menu `M`, then `18`, then `1`: it renames the folder
or file AND changes every place that uses the name (code, pages, photo lists,
database), with a backup first. The guides keep showing the first names;
`logs/renames.tsv` lists what is called what now. Guide 26.
"""),
("""**Moving the whole garden folder** (for example into `~/Projects/`):
1. Menu `3` (stop).
2. Move the folder.
3. Open the menu from its new place and choose `1` (install). It carries on,
   and fixes the desktop icon and the nightly backup line, which remember the
   folder's full path.
""",
"""**Moving the whole garden folder** (for example to a bigger disk): menu `M`, then
`18`, then `3`. It stops the garden, moves it, counts the files, and fixes the
desktop icon, the nightly backup line and Docker's programs, which remember the
folder's full path. Guide 26.
"""),
])

edit("ko/13-backups-and-folders.md", [
("""- 정원 폴더(사진, 백업 등) 하나를 옮기거나 이름을 바꾸려면 메뉴 `8`, 그다음 `8`을 쓰세요.
- 사진 폴더 안의 폴더나 사진 파일의 이름은 절대 직접 바꾸지 마세요.
""",
"""- 파일이나 폴더(사진, 페이지, 백업 등)의 이름을 바꾸거나 옮기려면 메뉴 `M`, 그다음 `18`(또는 메뉴 `8`, 그다음 `8`)을 쓰세요. 그것을 쓰는 모든 곳이 함께 바뀌어요: 안내서 26.
- 사진 폴더 안의 폴더나 사진 파일의 이름은 손으로 바꾸지 마세요: 그 메뉴를 쓰면 웹사이트와 데이터베이스가 따라와요.
"""),
("""정원이 쓰는 모든 폴더는 '한곳'에서 정해지고, **설정**(Settings)이 대신 바꿔 줘요:
메뉴 `8`, 그다음 `8`(Move or rename a folder, 폴더 옮기기 또는 이름 바꾸기). 정원을 멈추고,
폴더를 옮기고, 새 이름을 저장하고, 다시 시작해요. 그다음 점검표가 모든 폴더를 찾는지
확인해요.
""",
"""정원이 쓰는 모든 폴더는 '한곳'에서 정해지고, **설정**(Settings)이 대신 바꿔 줘요:
메뉴 `8`, 그다음 `8`(Rename or move a file or folder, 파일이나 폴더 이름 바꾸기 또는 옮기기),
그다음 `2`. 정원을 멈추고, 폴더를 옮기고(다른 디스크로도), 새 위치를 저장하고, 다시
시작해요. 그다음 점검표가 모든 폴더를 찾는지 확인해요. 안내서 26에 모든 단계가 있어요.
"""),
("""**이것들은 절대 손으로 이름을 바꾸지 마세요:**
- **사진 폴더 '안의' 폴더들**(`Database`, `Gallery`, `post`, `blog`,
  `plan`, `HeroBanner`). 웹사이트는 정확히 그 이름을 찾아요.
- **사진 파일 이름.** 데이터베이스는 각 사진을 이름으로 기억해요.
  이름을 바꾸면 페이지에 깨진 그림이 보여요. 대신 웹사이트에서 새 사진을 올리세요.
""",
"""**이것들은 손으로 이름을 바꾸지 마세요**(웹사이트가 잃어버려요):
- **사진 폴더 '안의' 폴더들**(`Database`, `Gallery`, `post`, `blog`,
  `plan`, `HeroBanner`). 웹사이트는 정확히 그 이름을 찾아요.
- **사진 파일 이름.** 데이터베이스는 각 사진을 이름으로 기억해요.

내 이름을 붙이려면 메뉴 `M`, 그다음 `18`, 그다음 `1`을 쓰세요: 폴더나 파일의 이름을
바꾸고, 그 이름을 쓰는 모든 곳(코드, 페이지, 사진 목록, 데이터베이스)도 바꿔요. 먼저
백업도 해요. 안내서에는 계속 처음 이름이 나와요. `logs/renames.tsv`에 지금 무엇이
무엇으로 불리는지 적혀 있어요. 안내서 26.
"""),
("""**정원 폴더 전체 옮기기**(예: `~/Projects/` 안으로):
1. 메뉴 `3`(멈추기).
2. 폴더를 옮기세요.
3. 새 위치에서 메뉴를 열고 `1`(설치)을 고르세요. 이어서 진행하면서, 폴더의 전체 경로를
   기억하는 바탕화면 아이콘과 매일 밤 백업 줄을 고쳐 줘요.
""",
"""**정원 폴더 전체 옮기기**(예: 더 큰 디스크로): 메뉴 `M`, 그다음 `18`, 그다음 `3`.
정원을 멈추고, 옮기고, 파일 수를 세고, 폴더의 전체 경로를 기억하는 바탕화면 아이콘,
매일 밤 백업 줄, Docker의 프로그램을 고쳐 줘요. 안내서 26.
"""),
])
print("ok")
