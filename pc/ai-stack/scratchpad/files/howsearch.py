import io
def edit(p, pairs):
    s = io.open(p, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) == 1, (p, a[:80], s.count(a2))
        s = s.replace(a2, b2)
    io.open(p, "w", encoding="utf-8", newline="").write(s)

EN = """## Searching the guides
The guides are many, so the handbook has a search box at the top of every page.
Open `00-WELCOME.html` (in the garden folder; on Windows it opens after the install).

1. Click the search box at the top (or press `/`). With nothing typed, it shows
   common topics: click one, for example **router**, **backup** or **rename**.
2. Or type any word, for example `password` or `NAS`. Several words find the places
   that have all of them: `backup Google Drive`.
3. Next to the box, choose the language: **English + 한국어**, **English** or **한국어**.
   The menu remembers your choice.
4. Click a result: the guide opens at that very section. `Enter` opens the first
   result; `Esc` clears the box.

It searches every guide here, in both languages, and the other guides too (the n8n
and project guides, what gets installed, the summary, the journal). It works on
this computer, with no internet, and nothing you type is sent anywhere.

"""
KO = """## 안내서 찾기
안내서가 많아서, 안내서 웹 페이지(handbook)에는 페이지마다 맨 위에 찾기 칸이 있어요.
`00-WELCOME.html`을 여세요(정원 폴더 안. Windows에서는 설치가 끝나면 열려요).

1. 맨 위의 찾기 칸을 클릭하세요(또는 `/`를 누르세요). 아무것도 입력하지 않으면 자주
   찾는 주제가 보여요: 하나를 클릭하세요. 예: **공유기**, **백업**, **이름 바꾸기**.
2. 또는 아무 낱말이나 입력하세요. 예: `password`, `NAS`, `비밀번호`. 여러 낱말을 쓰면
   그 낱말이 모두 있는 곳을 찾아요: `backup Google Drive`.
3. 칸 옆에서 언어를 고르세요: **English + 한국어**, **English**, **한국어**. 고른 것을 기억해요.
4. 결과를 클릭하면: 안내서가 바로 그 부분에서 열려요. `Enter`는 첫 결과를 열고,
   `Esc`는 칸을 비워요.

여기 있는 모든 안내서를 두 언어로 찾고, 다른 안내서(n8n과 프로젝트 안내서, 설치되는 것,
요약, 일지)도 찾아요. 이 컴퓨터에서 인터넷 없이 돌아가고, 입력한 것은 어디로도 보내지 않아요.

"""
edit("START-HERE/00-READ-ME-FIRST.md", [("## Looking after your garden\n", EN + "## Looking after your garden\n")])
edit("START-HERE/ko/00-READ-ME-FIRST.md", [("## 정원 돌보기\n", KO + "## 정원 돌보기\n")])
print("ok")
