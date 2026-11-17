find_download() {   # find_download FILE-NAME  -> prints the path, or returns 1
    local d
    # xdg-user-dir gives the real folder names: in a Korean Ubuntu they are
    # ~/다운로드 and ~/바탕화면, not ~/Downloads and ~/Desktop.
    for d in "$HERE" "$HOME" "$(xdg-user-dir DOWNLOAD 2>/dev/null)" "$(xdg-user-dir DESKTOP 2>/dev/null)" \
             "$HOME/Downloads" "$HOME/Desktop" "$(dirname "$HERE")"; do
        [ -n "$d" ] && [ -f "$d/$1" ] && { printf '%s\n' "$d/$1"; return 0; }
    done
    return 1
}
