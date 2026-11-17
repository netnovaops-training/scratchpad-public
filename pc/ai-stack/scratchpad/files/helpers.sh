# ---------- language: English, Korean, or both -----------------------------------
# Only the WORDS change. What you type (numbers, letters, y/n) stays the same.
# The Korean words live in lang/ko.tsv: one line per sentence,
# "English<TAB>Korean". A sentence that is not in that file shows in English.
# Your choice is saved in .garden-lang (en, ko or both). Menu: L.
LANG_MODE=$(cat .garden-lang 2>/dev/null || echo en)
case "$LANG_MODE" in en|ko|both) ;; *) LANG_MODE=en ;; esac
declare -A KO=()
load_lang() {
    KO=()
    [ "$LANG_MODE" = en ] && return 0
    [ -f lang/ko.tsv ] || return 0
    local en ko
    while IFS=$'\t' read -r en ko; do
        [ -n "$en" ] && [ "${en:0:1}" != "#" ] && KO["$en"]=$ko
    done < lang/ko.tsv
}
load_lang

# Lt "text": the text in the chosen language, as one line (for questions).
Lt() {
    local k=${KO[$1]:-}
    case "$LANG_MODE" in
        ko)   printf '%s' "${k:-$1}" ;;
        both) if [ -n "$k" ]; then printf '%s / %s' "$1" "$k"; else printf '%s' "$1"; fi ;;
        *)    printf '%s' "$1" ;;
    esac
}
# L "text": print a line in the chosen language. In "both" mode the Korean
# line comes right under the English one, a little dimmer. Keeps indentation.
L() {
    local s=$1 lead core k
    lead=${s%%[! ]*}; core=${s#"$lead"}; k=${KO[$core]:-}
    case "$LANG_MODE" in
        ko)   printf '%s%s\n' "$lead" "${k:-$core}" ;;
        both) printf '%s\n' "$s"; [ -n "$k" ] && printf '%s\033[2m%s\033[0m\n' "$lead" "$k" ;;
        *)    printf '%s\n' "$s" ;;
    esac
}
# Lblock: print every line of a block of text (a "here document") with L.
Lblock() { local line; while IFS= read -r line; do L "$line"; done; }

choose_language() {
    printf '\n   1) English\n   2) 한국어 (Korean)\n   3) Both / 둘 다\n'
    local c; read -r -p "   1, 2 or 3: " c
    case "$c" in 1) LANG_MODE=en ;; 2) LANG_MODE=ko ;; 3) LANG_MODE=both ;; *) return 0 ;; esac
    echo "$LANG_MODE" > .garden-lang
    load_lang
}

# ---------- small helpers ------------------------------------------------------
say()  { printf '\n\033[1;32m== %s\033[0m\n' "$(Lt "$*")"; }
note() { L "   $*"; }
warn() { printf '\033[1;33m%s:\033[0m %s\n' "$(Lt WARNING)" "$(Lt "$*")"; }
fail() { printf '\033[1;31m%s:\033[0m %s\n' "$(Lt STOPPED)" "$(Lt "$*")"; return 1; }
ask()  { local q=$1 def=${2:-} a; read -r -p "$(Lt "$q")${def:+ [$def]}: " a; printf '%s' "${a:-$def}"; }
confirm() {   # confirm "question" [default y|n]
    local def=${2:-y} a
    [ "${ASSUME_YES:-0}" = 1 ] && { [ "$def" = y ]; return; }
    read -r -p "$(Lt "$1") ($( [ "$def" = y ] && echo 'Y/n' || echo 'y/N' )): " a
    a=${a:-$def}; [ "${a,,}" = y ] || [ "${a,,}" = yes ]
}

# Show a command, then run it. You always see exactly what runs.
run() { printf '   \033[36m$ %s\033[0m\n' "$*"; "$@"; }

# The "open book" box shown before every install step.
explain() {   # explain "Title" "what it is" "why" "official page"
    printf '\n\033[1;34m+-- %s\033[0m\n' "$(Lt "$1")"
    printf '\033[1;34m|\033[0m  %s %s\n' "$(Lt What:)" "$(Lt "$2")"
    printf '\033[1;34m|\033[0m  %s %s\n' "$(Lt Why:)" "$(Lt "$3")"
    [ -n "${4:-}" ] && printf '\033[1;34m|\033[0m  %s %s\n' "$(Lt Read:)" "$4"
    printf '\033[1;34m+------\033[0m\n'
}
