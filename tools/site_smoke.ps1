<#
  site_smoke.ps1 -- hit every site host and main-site path, record the HTTP status (2026-11-13, Claude).
  Run before and after moving/renaming folders: a route that worked before and fails after = something
  the move broke. Results: D:\scratchpad\smoke\<date-time>.csv ; diff two runs with -Compare.

    powershell -File site_smoke.ps1                      run, print PASS/FAIL count
    powershell -File site_smoke.ps1 -Compare A.csv B.csv routes that got worse between two runs

  Hosts and paths are read from web/Caddyfile, so new routes are picked up without editing this file.
  Requests go to this PC's Caddy directly (curl --resolve host:443:127.0.0.1), so it tests the local stack,
  not the router. No redirects are followed: 30x (usually the SSO login) counts as alive.
  Alive = 200-499 except 404 (a 401/403 is a working lock). Broken = 404, 5xx, timeout.
#>
param([string[]]$Compare)
$out = 'D:\scratchpad\smoke'
New-Item -ItemType Directory -Force $out | Out-Null
if ($Compare) {
  $a = Import-Csv $Compare[0] | Group-Object url -AsHashTable -AsString
  Import-Csv $Compare[1] | Where-Object { $_.alive -eq 'False' -and $a[$_.url] -and $a[$_.url][0].alive -eq 'True' } |
    ForEach-Object { "WORSE  $($_.url)  $($a[$_.url][0].code) -> $($_.code)" }
  return
}
$caddy = Get-Content 'C:\Jasons New Site\www\ai-stack\web\Caddyfile'
$hosts = $caddy | Select-String '^([a-z0-9.-]+\.(duckdns|zapto)\.org)(:\d+)? *\{' | ForEach-Object { $_.Matches[0].Groups[1].Value + $_.Matches[0].Groups[3].Value } | Sort-Object -Unique
$paths = $caddy | Select-String '^\s+handle(_path)? (/[A-Za-z0-9_./-]+)' | ForEach-Object { $_.Matches[0].Groups[2].Value -replace '/\*?$', '/' } |
  Where-Object { $_ -notmatch 'callback|twiml|webhook|koalendar|__sso|/api/|/rest/' } | Sort-Object -Unique
$urls = @($hosts | ForEach-Object { "https://$_/" }) + @($paths | ForEach-Object { "https://example.org$_" })
$rows = foreach ($u in $urls) {
  $h = ([uri]$u).Host; $port = ([uri]$u).Port
  $r = & curl.exe -s -o NUL -w '%{http_code} %{time_total}' --max-time 15 -k --resolve "${h}:${port}:127.0.0.1" $u 2>$null
  $code, $t = "$r" -split ' '
  $c = [int]("0$code")
  [pscustomobject]@{ url = $u; code = $c; seconds = $t; alive = ($c -ge 200 -and $c -lt 500 -and $c -ne 404) }
}
$file = Join-Path $out ((Get-Date -Format 'yyyy-MM-dd_HHmm') + '.csv')
$rows | Export-Csv $file -NoTypeInformation
$bad = @($rows | Where-Object { -not $_.alive })
$bad | ForEach-Object { "BROKEN $($_.code) $($_.url)" }
"$(if ($bad) {'FAIL'} else {'PASS'}) $($rows.Count - $bad.Count)/$($rows.Count) alive -> $file"
