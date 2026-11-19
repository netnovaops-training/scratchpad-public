from pathlib import Path

p = "Garden-Menu.ps1"
s = Path(p).read_bytes().decode("utf-8")
def rep(a, b):
    global s
    assert s.count(a) == 1, a[:70]
    s = s.replace(a, b)
rep('''    [string]$WebName = "",''', '''    [string]$WebName = "",
    [ValidateSet("", "desktop", "engine")][string]$Engine = "",''')
rep('''$T = @{ mode = ""; ip = ""; user = ""; distro = $DefaultDistro; copyto = "" }''',
    '''$T = @{ mode = ""; ip = ""; user = ""; distro = $DefaultDistro; copyto = ""; engine = "engine" }''')
rep('''    if ($Distro) { $T.distro = $Distro }
}''', '''    if ($Distro) { $T.distro = $Distro }
    if ($Engine) { $T.engine = $Engine }
}''')
rep('''"distro=$($T.distro)", "copyto=$($T.copyto)") }''', '''"distro=$($T.distro)", "copyto=$($T.copyto)", "engine=$($T.engine)") }''')
rep('''        "wsl"    { return "this PC, inside Windows' Ubuntu ($($T.distro))" }''',
    '''        "wsl"    { if ($T.engine -eq "desktop") { return "this PC, inside Windows' Ubuntu ($($T.distro)), with Docker Desktop" } else { return "this PC, inside Windows' Ubuntu ($($T.distro))" } }''')
rep('''    } elseif ($c -eq "2") {
        $T.mode = "wsl"
    } else { return $false }''', '''    } elseif ($c -eq "2") {
        $T.mode = "wsl"
        Choose-Engine
    } else { return $false }''')
Path(p).write_text(s, encoding="utf-8", newline="")
print("ok")
