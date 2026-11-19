from pathlib import Path

p = "garden.sh"
s = Path(p).read_bytes().decode("utf-8")
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:70], s.count(a))
    s = s.replace(a, b)
rep("""#            bash garden.sh photos     load the photos download (now, or again)
""", """#            bash garden.sh photos     load the photos download (now, or again)
#            bash garden.sh rename     rename or move a file or folder (every place that uses it follows)
#            bash garden.sh off | on   switch everything off (nothing is deleted) or on again
#            bash garden.sh checkup    start again any program that is stuck
#            bash garden.sh cleanup    remove Docker's leftovers (never your files)
""")
rep("""        L "   8) Move or rename a folder"
""", """        L "   8) Rename or move a file or folder"
""")
rep("""            8)  do_move_folder; continue ;;
""", """            8)  do_rename; continue ;;
""")
rep("""  17) Reach the garden from anywhere, privately (Tailscale): on / off

   0) Back
EOF""", """  17) Reach the garden from anywhere, privately (Tailscale): on / off
  18) Rename or move a file or folder (every place that uses it follows)
  19) On / off switch, check-up and Docker clean-up

   0) Back
EOF""")
rep("""            17) do_tailscale ;;
            0|b|B) return 0 ;;""", """            17) do_tailscale ;;         18) do_rename ;;
            19) do_power ;;
            0|b|B) return 0 ;;""")
rep("""    start)      dc up -d && wait_site ;;""", """    start)      if garden_is_off; then power_on; else dc up -d && wait_site; fi ;;""")
rep("""    tailscale)  do_tailscale ;;
""", """    tailscale)  do_tailscale ;;
    rename)     do_rename ;;
    power)      do_power ;;
    off)        power_off ;;
    on)         power_on ;;
    checkup)    docker_checkup "${2:-}" ;;
    cleanup)    docker_cleanup ;;
""")
rep("""            2) run dc up -d && wait_site ;;
            3) run dc stop && note "Stopped. Nothing was deleted." ;;""",
"""            2) if garden_is_off; then power_on; else run dc up -d && wait_site; fi ;;
            3) run dc stop && note "Stopped. Nothing was deleted." ;;""")
rep("""do_backup() {
    say""", """do_backup() {
    # Switched off (More 19): nothing changes, so the nightly backup rests too.
    if [ "${1:-}" = nightly ] && garden_is_off; then note "The garden is switched off: no nightly backup needed."; return 0; fi
    say""")
rep("""( crontab -l 2>/dev/null | grep -v -e 'garden.sh backup' -e 'docker/backup.sh' ) | crontab -""",
"""( crontab -l 2>/dev/null | grep -v -e 'garden.sh backup' -e 'docker/backup.sh' -e 'garden.sh checkup' ) | crontab -""")
rep("""    # The picture folders the website expects. These inside names never change.
    for d in Database Gallery post blog plan HeroBanner; do mkdir -p "$(env_get IMAGES_DIR)/$d"; done""",
"""    # The picture folders the website expects (with their new names, if any
    # were renamed with Settings 8: IMAGE_FOLDERS in .env).
    local folders; folders=$(env_get IMAGE_FOLDERS); folders=${folders:-$IMAGE_FOLDERS_FIRST}
    for d in $folders; do mkdir -p "$(env_get IMAGES_DIR)/$d"; done""")
rep("""    has_part wiki && dc restart wiki >/dev/null 2>&1
    reload_signin
    wait_site
}""", """    has_part wiki && dc restart wiki >/dev/null 2>&1
    reload_signin
    wait_site
    # A backup from before a rename has the old names: offer to rename them again.
    redo_renames
}""")
rep("""    if photos_addon_load; then return 0; fi
    note "Not found.""", """    if photos_addon_load; then redo_renames; return 0; fi
    note "Not found.""")
Path(p).write_text(s, encoding="utf-8", newline="")
print("ok")
