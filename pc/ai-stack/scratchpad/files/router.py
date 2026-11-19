from pathlib import Path


def edit(p, pairs):
    s = Path(p).read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) == 1, (p, a[:80], s.count(a2))
        s = s.replace(a2, b2)
    Path(p).write_text(s, encoding="utf-8", newline="")

TABLE = """| Router | Address or app | User name and password | The maker's own steps |
|---|---|---|---|
| Netgear | `routerlogin.net` or `192.168.1.1`, or the Nighthawk app | `admin`, and the password set at setup | <https://kb.netgear.com/980/How-do-I-log-in-to-my-NETGEAR-router> |
| TP-Link (routers) | `tplinkwifi.net` or `192.168.1.1` | {tp} | <https://www.tp-link.com/us/support/faq/87/> |
| TP-Link Deco | the Deco app | {deco} | <https://www.tp-link.com/support/> |
| Asus | `www.asusrouter.com` or `192.168.1.1` | {asus} | <https://www.asus.com/us/support/faq/1005263/> |
| Linksys | `myrouter.local` or `192.168.1.1` | {linksys} | <https://support.linksys.com/kb/article/378-en/> |
| eero | the eero app | {eero} | <https://eero.com/support/articles/how-do-i-set-up-port-forwarding> |
| Google Nest Wifi / Google Wifi | the Google Home app | {google} | <https://support.google.com/googlehome/> |
| Xfinity | the Xfinity app, or `10.0.0.1` | {xfinity} | <https://www.xfinity.com/support/articles/admin-tool-access> |
| Spectrum | the My Spectrum app | {spectrum} | <https://www.spectrum.net/support/internet/managing-advanced-settings-my-spectrum-app> |
| AT&T | `192.168.1.254` | {att} | <https://www.att.com/support/article/u-verse-high-speed-internet/KM1206322/> |
| Verizon | `mynetworksettings.com` or `192.168.1.1` | `admin`, and the password on the label | <https://www.verizon.com/support/knowledge-base-308501/> |
"""
EN_CELLS = {"tp": "the first time you make one; some older ones use `admin` and `admin`",
                "deco": "your TP-Link account", "asus": "on the router's label (some use `admin`)",
                "linksys": "the router password (`admin` until you change it)", "eero": "your eero account",
                "google": "your Google account", "xfinity": "`admin`, and the password you make when you allow the Admin Tool in the app",
                "spectrum": "your Spectrum account", "att": "the Device Access Code on the gateway's label"}
KO_CELLS = {"tp": "처음에 직접 만들어요. 오래된 것은 `admin`과 `admin`을 쓰기도 해요",
                "deco": "내 TP-Link 계정", "asus": "공유기 라벨에 있어요(`admin`을 쓰는 것도 있어요)",
                "linksys": "공유기 비밀번호(바꾸기 전에는 `admin`)", "eero": "내 eero 계정",
                "google": "내 Google 계정", "xfinity": "`admin`, 그리고 앱에서 Admin Tool을 허용할 때 만드는 비밀번호",
                "spectrum": "내 Spectrum 계정", "att": "게이트웨이 라벨의 Device Access Code"}

EN = """## Before step 2: log in to your router (a walkthrough)
The router is the box your internet company gave you (or one you bought). Its
settings page lives inside your home: only a device on your home Wi-Fi (or on a
cable) can open it.

1. **Be at home**, on your home Wi-Fi.
2. **Find the router's address.** On the garden computer: menu `M`, then `2`
   ("Router address"). On a Windows PC: open PowerShell, type `ipconfig`, and read
   "Default Gateway". It is usually `192.168.1.1`, `192.168.1.1`, `192.168.1.1`,
   `10.0.0.1` or `192.168.1.254`.
3. **Type it in the browser's address bar** (not in the search box), like
   `http://192.168.1.1`, and press Enter. Many routers have a name for it too (the
   table below). A "Not secure" warning is normal for a router page at home:
   **Advanced**, then **Proceed**. Some routers have only a phone app instead.
4. **Sign in.** The user name and password are usually on a **label on the router**
   (often underneath), or they are the ones set when the router was installed.
   Routers from the internet company are often managed in its app, with your
   account there.
5. **Still the label's password? Change it** to your own and keep it in your
   password manager. Anyone on your Wi-Fi could otherwise change your router.
6. **Forgot it?** The router's own reset button (held about 10 seconds) brings back
   the label's password, but also the label's Wi-Fi name and password: every device
   at home must join again. Use it only as the last step.
7. Then step 2 below. Leave **Remote management** (managing the router from the
   internet) **off**.

Where to sign in on common home routers (menus change; the maker's own page is
the one to trust):

""" + TABLE.format(**EN_CELLS) + """
Not in the list? Search the maker's website for "log in to router" with the model
name from the label. Use the maker's own pages, not sites that list default passwords.

"""
KO = """## 2단계 전에: 공유기에 로그인하기(따라하기)
공유기는 인터넷 회사가 준(또는 내가 산) 상자예요. 설정 페이지는 집 안에만 있어요: 집
Wi-Fi(또는 케이블)에 연결된 기기만 열 수 있어요.

1. **집에서**, 집 Wi-Fi에 연결하세요.
2. **공유기 주소를 찾으세요.** 정원 컴퓨터에서: 메뉴 `M`, 그다음 `2`("Router address").
   Windows PC에서: PowerShell을 열고 `ipconfig`를 입력해서 "Default Gateway"(기본 게이트웨이)를
   보세요. 보통 `192.168.1.1`, `192.168.1.1`, `192.168.1.1`, `10.0.0.1`, `192.168.1.254` 중 하나예요.
3. **브라우저 주소창에 입력하세요**(검색창이 아니에요). 예: `http://192.168.1.1`, 그리고 Enter.
   이름으로 열리는 공유기도 많아요(아래 표). 집 공유기 페이지에서 "안전하지 않음" 경고는
   흔해요: **고급**, 그다음 **계속**. 휴대폰 앱만 있는 공유기도 있어요.
4. **로그인하세요.** 사용자 이름과 비밀번호는 보통 **공유기 라벨**(흔히 밑면)에 있거나, 공유기를
   설치할 때 정한 것이에요. 인터넷 회사의 공유기는 그 회사 앱에서 내 계정으로 다루는 경우가 많아요.
5. **아직 라벨의 비밀번호인가요? 바꾸세요.** 내 비밀번호로 바꾸고 비밀번호 관리자에 두세요.
   안 그러면 내 Wi-Fi에 있는 누구나 공유기를 바꿀 수 있어요.
6. **잊어버렸나요?** 공유기의 리셋 버튼(약 10초 누르기)은 라벨의 비밀번호로 되돌리지만, Wi-Fi
   이름과 비밀번호도 라벨의 것으로 돌아가요: 집의 모든 기기를 다시 연결해야 해요. 마지막 방법으로만 쓰세요.
7. 그다음 아래 2단계. **Remote management**(인터넷에서 공유기 관리)는 **꺼 두세요**.

흔한 집 공유기의 로그인 방법(메뉴는 바뀌어요. 믿을 것은 만든 회사의 페이지예요):

""" + TABLE.replace("| Router | Address or app | User name and password | The maker's own steps |",
                    "| 공유기 | 주소 또는 앱 | 사용자 이름과 비밀번호 | 만든 회사의 안내 |").replace(
                    "the Nighthawk app", "Nighthawk 앱").replace("the Deco app", "Deco 앱").replace(
                    "the eero app", "eero 앱").replace("the Google Home app", "Google Home 앱").replace(
                    "the Xfinity app, or", "Xfinity 앱, 또는").replace("the My Spectrum app", "My Spectrum 앱").replace(
                    "`admin`, and the password set at setup", "`admin`, 그리고 설치할 때 정한 비밀번호").replace(
                    "`admin`, and the password on the label", "`admin`, 그리고 라벨의 비밀번호").replace(
                    " or `", " 또는 `").replace(", or the", ", 또는").format(**KO_CELLS) + """
목록에 없나요? 만든 회사의 웹사이트에서 라벨의 모델 이름과 함께 "log in to router"를 찾아보세요.
기본 비밀번호를 모아 둔 사이트가 아니라, 만든 회사의 페이지를 쓰세요.

"""
edit("START-HERE/14-reserve-your-ip.md", [("## Step 2. Reserve it in the router", EN + "## Step 2. Reserve it in the router"),
    ("- Step 2: on the router's page, add a reservation with those two.",
     "- Step 2: on the router's page (how to log in: \"Before step 2\", with the common routers), add a reservation with those two.")])
s = Path("START-HERE/ko/14-reserve-your-ip.md").read_text(encoding="utf-8")
step2 = next(l for l in s.split("\n") if l.startswith("## 2단계")).rstrip("\r")
edit("START-HERE/ko/14-reserve-your-ip.md", [(step2, KO + step2)])
print("ok")
