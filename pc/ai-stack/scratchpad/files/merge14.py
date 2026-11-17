import io
def edit(p, pairs):
    s = io.open(p, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in s else "\n"
    for a, b in pairs:
        a2, b2 = a.replace("\n", nl), b.replace("\n", nl)
        assert s.count(a2) == 1, (p, a[:80], s.count(a2))
        s = s.replace(a2, b2)
    io.open(p, "w", encoding="utf-8", newline="").write(s)

NEW14 = "14) On / off: the whole garden, or only off the internet; check-up, clean-up"
edit("garden.sh", [
("  14) Turn the front door off (take the garden off the internet)\n", "  " + NEW14 + "\n"),
("  19) On / off switch, check-up and Docker clean-up\n", ""),
("            13) do_subdomains ;;        14) do_public_off ;;", "            13) do_subdomains ;;        14) do_power ;;"),
("            17) do_tailscale ;;         18) do_rename ;;\n            19) do_power ;;\n", "            17) do_tailscale ;;         18) do_rename ;;\n"),
("   3) Off the internet only: the garden keeps running at home (More 14)", "   3) Off the internet only: the garden keeps running at home (More 1 puts it back)"),
("   - On again: menu M, 19, then 2 (or menu 2, or: bash garden.sh on).", "   - On again: menu M, 14, then 2 (or menu 2, or: bash garden.sh on)."),
("    # Switched off (More 19): nothing changes", "    # Switched off (More 14): nothing changes"),
("# ---------- the on / off switch and Docker's check-up (More 19; garden.sh off | on | checkup | cleanup) ----",
 "# ---------- the on / off switch and Docker's check-up (More 14; garden.sh off | on | checkup | cleanup) ----"),
("(menu M, 14 switches it off)", "(menu M, 14, then 3 switches it off)"),
])
edit("lang/ko.tsv", [
("14) Turn the front door off (take the garden off the internet)\t14) 정문 끄기(정원을 인터넷에서 내리기)",
 NEW14 + "\t14) 켜기 / 끄기: 정원 전체, 또는 인터넷에서만 내리기; 점검, 정리"),
("3) Off the internet only: the garden keeps running at home (More 14)\t3) 인터넷에서만 내리기: 집에서는 정원이 계속 돌아가요(더 보기 14)",
 "3) Off the internet only: the garden keeps running at home (More 1 puts it back)\t3) 인터넷에서만 내리기: 집에서는 정원이 계속 돌아가요(더 보기 1로 다시 올려요)"),
("- On again: menu M, 19, then 2 (or menu 2, or: bash garden.sh on).\t- 다시 켜기: 메뉴 M, 19, 그다음 2 (또는 메뉴 2, 또는: bash garden.sh on).",
 "- On again: menu M, 14, then 2 (or menu 2, or: bash garden.sh on).\t- 다시 켜기: 메뉴 M, 14, 그다음 2 (또는 메뉴 2, 또는: bash garden.sh on)."),
("19) On / off switch, check-up and Docker clean-up\t19) 켜기 / 끄기 스위치, 점검, Docker 정리\n", ""),
])
edit("windows/Garden-Menu.ps1", [
("  15) Turn the front door off (take the garden off the internet)\n", "  15) On / off: the whole garden, or only off the internet; check-up, clean-up\n"),
("  21) On / off switch, check-up and Docker clean-up\n  22) Find the garden computer again (if its address changed)\n",
 "  21) Find the garden computer again (if its address changed)\n"),
('            "15" { Invoke-Garden "offline" | Out-Null }', '            "15" { Invoke-Garden "power" | Out-Null }'),
('            "21" { Invoke-Garden "power" | Out-Null }\n            "22" { Find-Garden-Again }', '            "21" { Find-Garden-Again }'),
("# More 22: find the garden computer again now", "# More 21: find the garden computer again now"),
])
print("ok")
