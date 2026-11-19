from pathlib import Path


def edit(p, pairs):
    s = Path(p).read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) == 1, (p, a[:70], s.count(a2))
        s = s.replace(a2, b2)
    Path(p).write_text(s, encoding="utf-8", newline="")

edit("14-reserve-your-ip.md", [
("""- New router? Do steps 2 and 3 again.
""", """- New router? Do steps 2 and 3 again.
- Router cannot reserve? The Windows menu finds the garden computer again by its network card when its address changes (More `22`). To go online, give it a fixed address on the computer itself: "If your router cannot reserve an address" below.
"""),
("""## If you change routers
Do steps 2 and 3 again on the new router. The garden computer itself needs nothing.
""", """## If you change routers
Do steps 2 and 3 again on the new router. The garden computer itself needs nothing.

## If your router cannot reserve an address
Some routers (often ones from the internet company) have no reservation page, or
it is locked. Then the garden computer's address may change after a restart.

**Your Windows PC keeps finding it anyway.** The guided installer's "Find it for
me" button lists the computers at home with the SSH door open (port 22), so you
can pick it. The Windows menu then remembers the garden computer by its network
card's fixed ID (the MAC address) and its name. Whenever nothing answers at the
saved address, it looks at home for the same network card and saves the new
address, before every job and before the weekly backup copy. To do it by hand:
Windows menu, `M`, then `22` (Find the garden computer again).

**To put the site online you still need a fixed address**, because the router
forwards ports 80 and 443 to one address. Give it to the computer itself:
1. On the router's page, find the range of addresses it hands out (often
   `192.168.1.100` to `192.168.1.200`). Pick an address outside it, for example
   `192.168.1.20`, that no other device uses.
2. On the garden computer: Settings, Network (or Wi-Fi), the gear next to your
   connection, IPv4, **Manual**. Address: the one you picked. Netmask:
   `255.255.255.0`. Gateway and DNS: the router's address (menu `M`, `2` shows it).
   Apply, then switch the connection off and on.
3. Run menu `M`, `2` again: the IP is the new one. Then do step 3 with it.

On an Ubuntu without a screen, the same is done with netplan:
<https://ubuntu.com/server/docs/configuring-networks>
"""),
])
edit("ko/14-reserve-your-ip.md", [
("""- 공유기를 바꿨나요? 2단계와 3단계를 다시 하세요.
""", """- 공유기를 바꿨나요? 2단계와 3단계를 다시 하세요.
- 공유기가 예약을 못 하나요? 주소가 바뀌면 Windows 메뉴가 네트워크 카드로 정원 컴퓨터를 다시 찾아요(더 보기 `22`). 인터넷에 올리려면 컴퓨터 자체에 고정 주소를 주세요: 아래의 "공유기가 주소를 예약하지 못하면".
"""),
("""## 공유기를 바꾸면
새 공유기에서 2단계와 3단계를 다시 하세요. 정원 컴퓨터에서는 할 일이 없어요.
""", """## 공유기를 바꾸면
새 공유기에서 2단계와 3단계를 다시 하세요. 정원 컴퓨터에서는 할 일이 없어요.

## 공유기가 주소를 예약하지 못하면
어떤 공유기는(인터넷 회사가 준 것에 많아요) 예약 페이지가 없거나 잠겨 있어요. 그러면
정원 컴퓨터의 주소가 다시 시작한 뒤에 바뀔 수 있어요.

**그래도 Windows PC는 계속 찾아요.** 안내 설치 프로그램의 "Find it for me"(찾아 줘요)
버튼이 SSH 문(포트 22)이 열린 집의 컴퓨터를 보여 줘서 고를 수 있어요. 그다음 Windows
메뉴가 정원 컴퓨터를 네트워크 카드의 고정 ID(MAC 주소)와 이름으로 기억해요. 저장한
주소에서 대답이 없으면, 모든 작업 전과 매주 백업 복사 전에 집에서 같은 네트워크 카드를
찾아 새 주소를 저장해요. 손으로 하려면: Windows 메뉴, `M`, 그다음 `22`(정원 컴퓨터 다시 찾기).

**사이트를 인터넷에 올리려면 그래도 고정 주소가 필요해요.** 공유기가 포트 80과 443을
한 주소로 보내기 때문이에요. 컴퓨터 자체에 주소를 주세요:
1. 공유기 페이지에서 공유기가 나눠 주는 주소 범위를 찾으세요(흔히 `192.168.1.100`에서
   `192.168.1.200`까지). 그 밖의 주소 하나를 고르세요. 예: 다른 기기가 쓰지 않는 `192.168.1.20`.
2. 정원 컴퓨터에서: Settings(설정), Network(네트워크, 또는 Wi-Fi), 연결 옆 톱니바퀴,
   IPv4, **Manual**(수동). 주소: 고른 주소. 넷마스크: `255.255.255.0`. 게이트웨이와 DNS:
   공유기 주소(메뉴 `M`, `2`가 보여 줘요). 적용한 다음 연결을 껐다 켜세요.
3. 메뉴 `M`, `2`를 다시 실행하세요: 새 IP가 보여요. 그 주소로 3단계를 하세요.

화면이 없는 Ubuntu에서는 netplan으로 똑같이 해요:
<https://ubuntu.com/server/docs/configuring-networks>
"""),
])

M = """### More 21) On / off switch, check-up and Docker clean-up
- **What it does:** The same as Ubuntu More 19, on the garden computer.
"""
edit("19-every-menu-option.md", [
("| 21 | On / off switch, check-up and Docker clean-up | 19 |\n",
 "| 21 | On / off switch, check-up and Docker clean-up | 19 |\n| 22 | Find the garden computer again (if its address changed) | (Windows only) |\n"),
(M, M + """
### More 22) Find the garden computer again (if its address changed)
- **What it does:** Checks the garden computer still answers at its saved address. If not, it looks at the computers at home with the SSH door open (port 22), finds the one with the same network card (MAC address) or name, and saves its new address. If it is not found, it lets you choose it again (with a list). The menu does this by itself before each job and before the weekly backup copy.
- **When to use it:** When your router cannot reserve an address, or after a new router.
- **What it installs or changes:** Only the saved address (and the network card ID and name) in `.garden-target-windows.txt`. It only knocks on port 22; nothing logs in.
- **Guide:** `14-reserve-your-ip.md`, "If your router cannot reserve an address".
"""),
])
MK = """### More 21) On / off switch, check-up and Docker clean-up — 켜기 / 끄기 스위치, 점검, Docker 정리
- **무엇을 하나요:** 정원 컴퓨터에서 Ubuntu 더 보기 19와 같은 일을 해요.
"""
edit("ko/19-every-menu-option.md", [
("| 21 | On / off switch, check-up and Docker clean-up(켜기 / 끄기 스위치, 점검, Docker 정리) | 19 |\n",
 "| 21 | On / off switch, check-up and Docker clean-up(켜기 / 끄기 스위치, 점검, Docker 정리) | 19 |\n| 22 | Find the garden computer again(정원 컴퓨터 다시 찾기) | (Windows만) |\n"),
(MK, MK + """
### More 22) Find the garden computer again (if its address changed) — 정원 컴퓨터 다시 찾기(주소가 바뀌었으면)
- **무엇을 하나요:** 정원 컴퓨터가 저장한 주소에서 아직 대답하는지 확인해요. 아니면 SSH 문(포트 22)이 열린 집의 컴퓨터를 살펴서, 같은 네트워크 카드(MAC 주소)나 이름을 가진 것을 찾아 새 주소를 저장해요. 못 찾으면 목록에서 다시 고르게 해요. 메뉴는 모든 작업 전과 매주 백업 복사 전에 이것을 스스로 해요.
- **언제 쓰나요:** 공유기가 주소를 예약하지 못할 때, 또는 공유기를 바꾼 뒤.
- **무엇을 설치하거나 바꾸나요:** `.garden-target-windows.txt`의 저장된 주소(그리고 네트워크 카드 ID와 이름)만. 포트 22를 두드려 보기만 하고, 로그인하지 않아요.
- **안내서:** [`14-reserve-your-ip.md`](14-reserve-your-ip.md), "공유기가 주소를 예약하지 못하면".
"""),
])
print("ok")
