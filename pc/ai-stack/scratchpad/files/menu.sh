# ---------- the menu -------------------------------------------------------------------
banner() {
    clear 2>/dev/null || true
    printf '\033[1;32m  ==============================================\n     Robin'"'"'s Garden%s\n  ==============================================\033[0m\n' "${1:+  -  $1}"
    echo "  This computer: $(hostname)   $(hostname -I 2>/dev/null | awk '{print $1}')"
    if [ -f .env ]; then
        echo "  Parts on: core$( [ -n "$(env_get COMPOSE_PROFILES)" ] && echo ",$(env_get COMPOSE_PROFILES)")   Website: $(site_url)"
    else
        echo "  Not installed yet: choose 1."
    fi
}

menu() {
    while true; do
        banner
        cat <<'EOF'

   1) INSTALL  (first time, or carry on, or repair)

   2) Start the garden          3) Stop the garden
   4) Open the website          5) Checklist: is everything OK?
   6) Back up now               7) Restore a backup
   8) Settings: names, emails, passwords, keys, folders
   9) Parts: garden tools, wiki, n8n (on / off)

   H) Help file for someone helping you (no passwords in it)
   M) More (advanced)
   0) Quit
EOF
        local c; read -r -p $'\n  Type a number or letter, then press Enter: ' c
        case "$c" in
            1) do_install ;;
            2) run dc up -d && wait_site ;;
            3) run dc stop && note "Stopped. Nothing was deleted." ;;
            4) xdg-open "$(site_url)" >/dev/null 2>&1 & note "Opening $(site_url)" ;;
            5) do_check ;;
            6) do_backup ;;              7) do_restore ;;
            8) do_settings ;;            9) do_parts ;;
            h|H) do_helpme ;;
            m|M) menu_more; continue ;;
            0|q|Q) exit 0 ;;
            *) note "Please type one of the numbers or letters shown." ;;
        esac
        pause
    done
}

menu_more() {
    while true; do
        banner "More"
        cat <<'EOF'

   1) The front door: HTTPS, your sign-in page, your web name
   2) Network: my IP address and reserving it in the router
   3) Email (Gmail) for password links, with a test email
   4) Set my password (website + sign-in page)
   5) Install on ANOTHER computer (over SSH)
   6) Show the programs' recent messages (logs)
   7) Rebuild after editing Rust files
   8) Look inside the database (Adminer web page)
   9) Connect a local AI computer
  10) Desktop icon
  11) Uninstall

   0) Back
EOF
        local c; read -r -p $'\n  Type a number, then press Enter: ' c
        case "$c" in
            1) do_public ;;              2) do_network ;;
            3) do_email ;;               4) do_password ;;
            5) do_remote ;;              6) dc logs --tail 50 ;;
            7) do_rebuild ;;             8) do_dbweb ;;
            9) do_ai ;;                  10) make_icon ;;
            11) do_uninstall ;;
            0|b|B) return 0 ;;
            *) note "Please type one of the numbers shown." ;;
        esac
        pause
    done
}
