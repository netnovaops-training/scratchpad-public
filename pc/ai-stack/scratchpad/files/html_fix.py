import io
def edit(p, pairs):
    s = io.open(p, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) == 1, (p, a[:80], s.count(a2))
        s = s.replace(a2, b2)
    io.open(p, "w", encoding="utf-8", newline="").write(s)

edit("00-READ-ME-FIRST.html", [
("""        <strong>local IP address</strong> (on that computer type <code>hostname -I</code>; it looks like <code>192.168.1.50</code>)
        and your user name there.""", """        <strong>local IP address</strong> (on that computer type <code>hostname -I</code>; it looks like <code>192.168.1.50</code>,
        or click <strong>Find it for me</strong> and pick it from the list) and your user name there."""),
("""        (To learn more: <a href="https://learn.microsoft.com/windows/wsl/networking">WSL networking, by Microsoft</a>;
        the garden does not set this up.)</div>""", """        (To learn more: <a href="https://learn.microsoft.com/windows/wsl/networking">WSL networking, by Microsoft</a>;
        the garden does not set this up.)
        <br>Then it asks which Docker runs the garden's programs: <strong>Docker Desktop</strong>, the easy way, with buttons
        to see, start and stop them (the installer gets it for you), or <strong>Docker inside Ubuntu</strong>, the
        hands-on way. Guide 26 compares them.</div>"""),
("""    The front door: HTTPS (Caddy), your sign-in page (Authelia), your web name (No-IP), router ports 80 and 443. Also n8n, the database viewer, local AI.</div>""",
 """    The front door: HTTPS (Caddy), your sign-in page (Authelia), your web name (No-IP), router ports 80 and 443. Also n8n, the database viewer, local AI, Tailscale (reach it from anywhere), your own names for folders and pages, and the on / off switch (guide 26).</div>"""),
])
edit("00-READ-ME-FIRST-KO.html", [
("""        <strong>로컬 IP 주소</strong>(그 컴퓨터에서 <code>hostname -I</code>를 입력하세요. <code>192.168.1.50</code>처럼 생겼어요)와
        그 컴퓨터의 사용자 이름을 입력해요.""", """        <strong>로컬 IP 주소</strong>(그 컴퓨터에서 <code>hostname -I</code>를 입력하세요. <code>192.168.1.50</code>처럼 생겼어요.
        또는 <strong>Find it for me</strong>(찾아 줘요)를 눌러 목록에서 고르세요)와 그 컴퓨터의 사용자 이름을 입력해요."""),
("""        (더 알고 싶다면: <a href="https://learn.microsoft.com/windows/wsl/networking">WSL 네트워크, Microsoft</a>.
        정원은 이것을 설정하지 않아요.)</div>""", """        (더 알고 싶다면: <a href="https://learn.microsoft.com/windows/wsl/networking">WSL 네트워크, Microsoft</a>.
        정원은 이것을 설정하지 않아요.)
        <br>그다음 정원의 프로그램을 어떤 Docker로 돌릴지 물어요: <strong>Docker Desktop</strong>은 쉬운 방법이에요(버튼으로
        보고, 시작하고, 멈춰요. 설치 프로그램이 대신 받아 줘요). <strong>Ubuntu 안의 Docker</strong>는 직접 하는
        방법이에요. 안내서 26에서 둘을 비교해요.</div>"""),
("""    정문(front door): HTTPS(Caddy), 로그인 페이지(Authelia), 웹 이름(No-IP), 공유기 포트 80과 443. 그리고 n8n, 데이터베이스 보기 프로그램, 로컬 AI.</div>""",
 """    정문(front door): HTTPS(Caddy), 로그인 페이지(Authelia), 웹 이름(No-IP), 공유기 포트 80과 443. 그리고 n8n, 데이터베이스 보기 프로그램, 로컬 AI, Tailscale(어디서나 접속), 폴더와 페이지에 내 이름 붙이기, 켜기 / 끄기 스위치(안내서 26).</div>"""),
])
edit("00-HOW-IT-WORKS.html", [("More: the front door, email, network, logs, database, local AI, subdomains, LiteLLM, the Website name swap, uninstall (Windows: shortcuts too)",
     "More: the front door, email, network, logs, database, local AI, subdomains, the on / off switch, LiteLLM, the Website name swap, Tailscale, renaming and moving, uninstall (Windows: the weekly copy, shortcuts, finding the garden computer again too)")])
edit("00-HOW-IT-WORKS-KO.html", [("More(더 보기): 정문, 이메일, 네트워크, 로그, 데이터베이스, 로컬 AI, 하위 도메인, LiteLLM, 웹사이트 이름 바꾸기, 제거(Windows: 바로가기도)",
     "More(더 보기): 정문, 이메일, 네트워크, 로그, 데이터베이스, 로컬 AI, 하위 도메인, 켜기 / 끄기 스위치, LiteLLM, 웹사이트 이름 바꾸기, Tailscale, 이름 바꾸기와 옮기기, 제거(Windows: 매주 사본, 바로가기, 정원 컴퓨터 다시 찾기도)")])

# The summary page: the counts as they are now.
edit("HANDOVER-SUMMARY.md", [("- **Guides:** 28 in English and 26 in Korean, plus 12 project guides; a beginner's\n  bash walkthrough; 41 pictures taken from the real program,",
     "- **Guides:** 30 in English and 28 in Korean, plus 12 project and automation guides; a\n  beginner's bash walkthrough; 46 pictures taken from the real program,")])

# n8n: the menu's own n8n part is the main way.
s = io.open("START-HERE/n8n/N1-install-and-first-email.md", encoding="utf-8").read()
print(s[:900])
print("ok")
