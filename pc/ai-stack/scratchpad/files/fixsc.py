import io
p = "garden.sh"
s = io.open(p, encoding="utf-8", newline="").read()
def rep(a, b):
    global s
    assert s.count(a) == 1, a[:60]
    s = s.replace(a, b)
rep('''    case "$p" in "~") p=$HOME ;; "~/"*) p=$HOME/${p#"~/"} ;; esac''',
    '''    # shellcheck disable=SC2088  # the ~ is matched as typed, then swapped for $HOME
    case "$p" in "~") p=$HOME ;; "~/"*) p=$HOME/${p#"~/"} ;; esac''')
rep('''key root rel nkey nroot nrel web nweb dbold= dbnew= other site f''', '''key root rel nkey nrel web nweb dbold='' dbnew='' other site f''')
rep('''code_n=0 data_n=0 db_n=0 rebuilt= check''', '''code_n=0 data_n=0 db_n=0 rebuilt='' check''')
rep('''read -r nkey nroot nrel < <(rename_area "$new")''', '''read -r nkey _ nrel < <(rename_area "$new")''')
rep('''        mkdir -p "$keep/files/$(dirname "${f#/}")" && cp -p "$f" "$keep/files/${f#/}" ||
            { fail "could not keep a copy of $f, so nothing was changed"; dc up -d >/dev/null; return 1; }''',
'''        if ! { mkdir -p "$keep/files/$(dirname "${f#/}")" && cp -p "$f" "$keep/files/${f#/}"; }; then
            fail "could not keep a copy of $f, so nothing was changed"; dc up -d >/dev/null; return 1
        fi''')
rep('''        env_set IMAGE_FOLDERS "$(printf''', '''        # shellcheck disable=SC2086  # one folder name per line
        env_set IMAGE_FOLDERS "$(printf''')
rep('''        cd "$old" 2>/dev/null; fail "the move failed''', '''        cd "$old" 2>/dev/null || true; fail "the move failed''')
rep('''    [ "$before" = "$after" ] && note "All $after files and folders arrived." || warn "$before files and folders before, $after after. Look before deleting anything."''',
'''    if [ "$before" = "$after" ]; then note "All $after files and folders arrived."
    else warn "$before files and folders before, $after after. Look before deleting anything."; fi''')
rep('''    local since= caddy=0 days plants''', '''    local since='' caddy=0 days plants''')
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("ok")
