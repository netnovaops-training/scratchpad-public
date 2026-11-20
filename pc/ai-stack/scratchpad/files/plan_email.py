from pathlib import Path


def edit(p, pairs):
    s = Path(p).read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) == 1, (p, a[:80], s.count(a2))
        s = s.replace(a2, b2)
    Path(p).write_text(s, encoding="utf-8", newline="")

EN = """## Plan your website's name and email (10 minutes, on paper)

**1. A name.** Short, easy to say on the phone, lowercase letters, numbers and `-`
only, for example `momsgarden`. It becomes your web name (`momsgarden.example.org`,
guide 04) and, if it is free, your website's Gmail (`user11@example.com`).
Check both are free before you decide.

**2. An email.** Three choices; each works:

| Choice | Good | Less good |
|---|---|---|
| Your own email | Nothing new to make | The website's mail mixes with yours; handing the website on later is harder |
| One you already use for sign-ups (or spam) | Keeps your main inbox quiet | Easy to miss the important mails (password links, No-IP's 30-day email) |
| **A new Gmail just for the website** (recommended): <https://accounts.google.com/signup> | Everything in one place, apart from your own mail; easy to hand on | One more password (keep it in your password manager) |

**3. One Gmail, many addresses ("plus addresses").** Gmail delivers
`user12@example.com` to `user11@example.com`: add `+` and any word
before the `@`. Give each service its own word when you sign up:

| Service | Sign up with |
|---|---|
| No-IP (web name) | `user12@example.com` |
| DuckDNS, Tailscale | `user13@example.com`, `user14@example.com` |
| Google AI Studio, OpenRouter | `user15@example.com` |

The "To" line of each email then shows which service it came through, and spam to
`+noip` tells you who passed your address on. To sort them for you: in Gmail, the
search box, **Show search options**, type the address under **To**, **Create filter**,
then choose a label (for example "No-IP"). Google's steps:
<https://support.google.com/a/users/answer/9308648> and <https://support.google.com/mail/answer/6579>.

Good to know:
- A few websites refuse a `+` in an address; then use the plain one.
- Someone can remove the `+word`, so it tells you where an address leaked, but does not hide it.
- Dots do not matter in Gmail (`momsgarden.site` = `momsgardensite`):
  <https://support.google.com/mail/answer/7436150>.
- The Gmail app password (menu `M`, `3`) belongs to the plain address: type that one there.
- The garden's own sign-in is always your garden password, never this email account.

"""
KO = """## 내 웹사이트의 이름과 이메일 정하기(10분, 종이에)

**1. 이름.** 짧고, 전화로 말하기 쉽고, 소문자, 숫자, `-`만 써요. 예: `momsgarden`.
이것이 웹 이름(`momsgarden.example.org`, 안내서 04)이 되고, 비어 있으면 웹사이트의
Gmail(`user11@example.com`)도 돼요. 정하기 전에 둘 다 비어 있는지 확인하세요.

**2. 이메일.** 세 가지 중 어느 것이든 돼요:

| 선택 | 좋은 점 | 덜 좋은 점 |
|---|---|---|
| 내 이메일 | 새로 만들 것이 없어요 | 웹사이트 메일이 내 메일과 섞여요. 나중에 웹사이트를 넘겨주기가 어려워요 |
| 가입용으로(또는 스팸용으로) 이미 쓰는 이메일 | 주 메일함이 조용해요 | 중요한 메일(비밀번호 링크, No-IP의 30일 메일)을 놓치기 쉬워요 |
| **웹사이트만을 위한 새 Gmail**(추천): <https://accounts.google.com/signup> | 모두 한곳에, 내 메일과 따로. 넘겨주기 쉬워요 | 비밀번호가 하나 더 생겨요(비밀번호 관리자에 두세요) |

**3. Gmail 하나, 주소 여러 개("+ 주소").** Gmail은 `user12@example.com`을
`user11@example.com`으로 보내 줘요: `@` 앞에 `+`와 아무 낱말이나 붙이면 돼요.
가입할 때 서비스마다 다른 낱말을 주세요:

| 서비스 | 가입할 주소 |
|---|---|
| No-IP(웹 이름) | `user12@example.com` |
| DuckDNS, Tailscale | `user13@example.com`, `user14@example.com` |
| Google AI Studio, OpenRouter | `user15@example.com` |

그러면 메일마다 "받는 사람(To)" 줄이 어느 서비스로 왔는지 보여 주고, `+noip`로 온 스팸은
누가 내 주소를 넘겼는지 알려 줘요. 자동으로 나누려면: Gmail 검색창, **Show search options**(검색
옵션 표시), **To**(받는 사람)에 주소 입력, **Create filter**(필터 만들기), 그다음 라벨을
고르세요(예: "No-IP"). Google의 안내:
<https://support.google.com/a/users/answer/9308648>, <https://support.google.com/mail/answer/6579>.

알아 두기:
- `+`가 들어간 주소를 받지 않는 웹사이트도 있어요. 그때는 그냥 주소를 쓰세요.
- 누군가 `+낱말`을 지울 수 있어요. 주소가 어디서 샜는지는 알려 주지만, 숨겨 주지는 않아요.
- Gmail에서는 점이 상관없어요(`momsgarden.site` = `momsgardensite`):
  <https://support.google.com/mail/answer/7436150>.
- Gmail 앱 비밀번호(메뉴 `M`, `3`)는 그냥 주소의 것이에요: 거기에는 그 주소를 입력하세요.
- 정원 자체의 로그인은 언제나 내 정원 비밀번호예요. 이 이메일 계정이 아니에요.

"""
edit("START-HERE/00b-before-you-start.md", [("## Optional (only if you want them)", EN + "## Optional (only if you want them)")])
edit("START-HERE/ko/00b-before-you-start.md", [("## 선택(원할 때만)", KO + "## 선택(원할 때만)")])
edit("START-HERE/25-common-questions.md", [("**More:** [guide 00b](00b-before-you-start.md)\n\n### Where are my passwords",
     "One Gmail can also give each service its own address (`user16@example.com`), so you see who\nsends what: guide 00b, \"Plan your website's name and email\".\n\n**More:** [guide 00b](00b-before-you-start.md)\n\n### Where are my passwords")])
edit("START-HERE/ko/25-common-questions.md", [("**더 보기:** [안내서 00b](00b-before-you-start.md)\n\n### 비밀번호와 키는",
     "Gmail 하나로 서비스마다 다른 주소(`user16@example.com`)를 줄 수도 있어서, 누가 무엇을 보내는지\n보여요: 안내서 00b, \"내 웹사이트의 이름과 이메일 정하기\".\n\n**더 보기:** [안내서 00b](00b-before-you-start.md)\n\n### 비밀번호와 키는")])
print("ok")
