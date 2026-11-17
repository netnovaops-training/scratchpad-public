import io
def edit(p, pairs):
    s = io.open(p, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) == 1, (p, a[:80], s.count(a2))
        s = s.replace(a2, b2)
    io.open(p, "w", encoding="utf-8", newline="").write(s)

H = "START-HERE/"
edit(H + "00b-before-you-start.md", [
("| What | Why | Time |\n|---|---|---|\n| **A Gmail app password**",
 "| What | Why | Time |\n|---|---|---|\n"
 "| **A Gmail just for your website** (recommended, free): <https://accounts.google.com/signup> | One account for everything the website needs: it sends the password emails, keeps the Google Drive backups and the AI key, and signs in to Tailscale, DuckDNS and No-IP (they offer \"Sign in with Google\"). Your personal inbox stays apart, and handing the website on later means handing on one account | About 10 minutes |\n"
 "| **A Gmail app password**"),
])
edit(H + "ko/00b-before-you-start.md", [
("| 무엇 | 왜 | 시간 |\n|---|---|---|\n| **Gmail 앱 비밀번호**",
 "| 무엇 | 왜 | 시간 |\n|---|---|---|\n"
 "| **웹사이트만을 위한 Gmail**(추천, 무료): <https://accounts.google.com/signup> | 웹사이트에 필요한 모든 것을 한 계정으로: 비밀번호 이메일을 보내고, Google Drive 백업과 AI 키를 두고, Tailscale, DuckDNS, No-IP에 로그인해요(\"Sign in with Google\"을 써요). 개인 메일함은 따로 지켜지고, 나중에 웹사이트를 넘겨줄 때도 계정 하나만 넘기면 돼요 | 약 10분 |\n"
 "| **Gmail 앱 비밀번호**"),
])
edit(H + "25-common-questions.md", [
("- Where are my passwords and keys kept?\n", "- Where are my passwords and keys kept?\n- Should the website have its own Gmail?\n"),
("### Where are my passwords and keys kept?", """### Should the website have its own Gmail?
It is a good idea, and free: <https://accounts.google.com/signup>. Use it for the
website's email (menu `M`, `3`), the Google Drive backups, the AI key, and to sign in
to Tailscale, DuckDNS and No-IP with "Sign in with Google". Your personal inbox stays
apart, and handing the website on one day means handing on one account. Turn on its
2-Step Verification. The garden's own sign-in stays your garden password: no outside
account can open the garden.

**More:** [guide 00b](00b-before-you-start.md)

### Where are my passwords and keys kept?"""),
])
edit(H + "ko/25-common-questions.md", [
("- 비밀번호와 키는 어디에 보관되나요?\n", "- 비밀번호와 키는 어디에 보관되나요?\n- 웹사이트만의 Gmail이 있으면 좋나요?\n"),
("### 비밀번호와 키는 어디에 보관되나요?", """### 웹사이트만의 Gmail이 있으면 좋나요?
좋은 생각이고 무료예요: <https://accounts.google.com/signup>. 웹사이트의 이메일(메뉴 `M`, `3`),
Google Drive 백업, AI 키에 쓰고, "Sign in with Google"로 Tailscale, DuckDNS, No-IP에
로그인하세요. 개인 메일함은 따로 지켜지고, 언젠가 웹사이트를 넘겨줄 때 계정 하나만 넘기면
돼요. 그 계정의 2단계 인증을 켜세요. 정원 자체의 로그인은 내 정원 비밀번호 그대로예요:
바깥 계정으로는 정원을 열 수 없어요.

**더 보기:** [안내서 00b](00b-before-you-start.md)

### 비밀번호와 키는 어디에 보관되나요?"""),
])
edit("garden.sh", [
("""   Nothing is installed for this: Gmail's own mail server sends the emails.
""", """   Nothing is installed for this: Gmail's own mail server sends the emails.
   Tip: a Gmail just for your website keeps your personal inbox apart
   (free: https://accounts.google.com/signup ; guide 00b).
"""),
])
s = io.open("lang/ko.tsv", encoding="utf-8", newline="").read()
nl = "\r\n" if "\r\n" in s else "\n"
s = s.rstrip("\r\n") + nl + "Tip: a Gmail just for your website keeps your personal inbox apart\t팁: 웹사이트만을 위한 Gmail을 쓰면 개인 메일함이 따로 지켜져요" + nl + \
    "(free: https://accounts.google.com/signup ; guide 00b).\t(무료: https://accounts.google.com/signup ; 안내서 00b)." + nl
io.open("lang/ko.tsv", "w", encoding="utf-8", newline="").write(s)
print("ok")
