import io
def edit(p, pairs):
    s = io.open(p, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) == 1, (p, a[:80], s.count(a2))
        s = s.replace(a2, b2)
    io.open(p, "w", encoding="utf-8", newline="").write(s)

ADM_EN = "| Adminer | A one-page web viewer for the database | Look inside the tables without typing SQL | Docker, `127.0.0.1:8082`, only while you use it | <https://www.adminer.org> |\n"
edit("WHAT-GETS-INSTALLED.md", [
(ADM_EN, ADM_EN +
 "| LiteLLM (menu `M`, `15`) | One private address for AI, with a monthly spending limit | So a paid AI key can never cost more than you chose | Docker, `127.0.0.1:4000` | <https://docs.litellm.ai> |\n"
 "| DuckDNS updater (menu `M`, `13`) | One cron line that tells DuckDNS your home address, for subdomains | Free extra names (wiki., n8n., ...) behind your sign-in page | Ubuntu (cron) | <https://www.example.net/install.jsp> |\n"
 "| Tailscale (menu `M`, `17`) | Tailscale's own program: a private network of only your devices | Reach the garden computer from anywhere, with no port opened in the router | Ubuntu | <https://tailscale.com/kb/1017/install> |\n"),
("| 8030, 8025, 3000, 9091, 5678, 8082 | website, garden tools, wiki, sign-in page, n8n, Adminer |",
 "| 8030, 8025, 3000, 9091, 5678, 8082, 4000 | website, garden tools, wiki, sign-in page, n8n, Adminer, LiteLLM |"),
])
s = io.open("WHAT-GETS-INSTALLED.ko.md", encoding="utf-8").read()
adm_ko = [l for l in s.split("\n") if l.startswith("| Adminer |")][0].rstrip("\r") + "\n"
edit("WHAT-GETS-INSTALLED.ko.md", [
(adm_ko, adm_ko +
 "| LiteLLM(메뉴 `M`, `15`) | 월 지출 한도가 있는, AI를 위한 개인 주소 하나 | 유료 AI 키가 내가 정한 것보다 더 쓰지 못하게 | Docker, `127.0.0.1:4000` | <https://docs.litellm.ai> |\n"
 "| DuckDNS 업데이트 프로그램(메뉴 `M`, `13`) | 하위 도메인을 위해 내 집 주소를 DuckDNS에 알려 주는 cron 한 줄 | 로그인 페이지 뒤의 무료 추가 이름(wiki., n8n., ...) | Ubuntu(cron) | <https://www.example.net/install.jsp> |\n"
 "| Tailscale(메뉴 `M`, `17`) | Tailscale 자체 프로그램: 내 기기만 들어오는 개인 네트워크 | 공유기에 포트를 열지 않고 어디서나 정원 컴퓨터에 닿기 | Ubuntu | <https://tailscale.com/kb/1017/install> |\n"),
("| 8030, 8025, 3000, 9091, 5678, 8082 | 웹사이트, 정원 도구, 위키, 로그인 페이지, n8n, Adminer |",
 "| 8030, 8025, 3000, 9091, 5678, 8082, 4000 | 웹사이트, 정원 도구, 위키, 로그인 페이지, n8n, Adminer, LiteLLM |"),
("- 지우려면: 메뉴 `M`, 그다음 Uninstall(제거).", "- 지우려면: 메뉴 `M`, 그다음 `12`(Uninstall, 제거)."),
("""메뉴 `M`, 그다음 `Uninstall`(제거하기)은 정원의 프로그램, 매일 밤 백업, 아이콘을
멈추고 지워요. 사진, 데이터베이스, 백업은 남겨 둬요. Docker, Caddy, No-IP 업데이트
프로그램을 지우는 명령은 화면에 보여 주기만 해요. 지울지는 본인이 정해요.""",
"""메뉴 `M`, 그다음 `12`(Uninstall, 제거하기)는 정원의 프로그램, 매일 밤 백업, 아이콘을
멈추고 지우고, Caddy와 No-IP 업데이트 프로그램도 지워요. 사진, 데이터베이스, 백업은
남겨 둬요. Docker를 지우는 명령은 화면에 보여 주기만 해요. 지울지는 본인이 정해요."""),
])

# The summary page, the journal and the change log: More 14 is now the switch.
edit("HANDOVER-SUMMARY.md", [
("- **An on / off switch** (menu M, 19): everything off", "- **An on / off switch** (menu M, 14): everything off"),
("a clean-up that removes only Docker's leftovers.", "a clean-up that removes only Docker's leftovers older than 7 days, after asking."),
])
edit("JOURNAL.md", [("Menu M, 19 switches everything off", "Menu M, 14 switches everything off")])
edit("JOURNAL.ko.md", [("메뉴 M, 19가 아무것도 지우지 않고", "메뉴 M, 14가 아무것도 지우지 않고")])
edit("CHANGES.md", [
("| **On / off switch** (menu M, 19; `bash garden.sh off` / `on`) |", "| **On / off switch** (menu M, 14, which also holds the old \"front door off\" as choice 3; `bash garden.sh off` / `on`) |"),
("| **Check-up** (menu M, 19, 4; every 6 hours if switched on) |", "| **Check-up** (menu M, 14, 4; every 6 hours if switched on) |"),
("| **Clean-up** (menu M, 19, 5) |", "| **Clean-up** (menu M, 14, 5) |"),
("Removes only old versions of the garden's own images and build notes older than 30 days, after showing the list.",
 "Removes only old versions of the garden's own images older than 7 days (a recent rebuild's old version stays, in case it is needed to go back) and build notes older than 30 days, after showing the list; Enter means no."),
("| **Windows menu: More 20 and 21** | The same as Ubuntu More 18 and 19 | More 20 also copies",
 "| **Windows menu: More 15 and 20** | The same as Ubuntu More 14 and 18 | More 20 also copies"),
("(automatic; Windows More 22)", "(automatic; Windows More 21)"),
])
print("ok")
