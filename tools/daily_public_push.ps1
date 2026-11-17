<#
daily_public_push.ps1 - twice a day: refresh the cleaned public copy from the Scratchpad, send it to Gitea,
then push it to GitHub (public) only if every check is clean.

How to run:   powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\daily_public_push.ps1
Scheduled:    Windows task "Scratchpad public GitHub push", 09:35 and 21:35 (5 minutes after the private push).
Reads/writes: D:\scratchpad (source, read only), D:\scratchpad-public (git), remotes nuc (-> Gitea
              Jason/scratchpad-public via the NUC's post-receive hook) and github
              (github.com/netnovaops-training/scratchpad-public, PUBLIC). Log: D:\scratchpad-public\.git\daily-push.log
How to test:  run it by hand; the log ends with "github: pushed <sha>" or "github: up to date".

Guards, in order (any failure stops before GitHub):
  1. public_copy.py --write: replaces personal words and stops if a BANNED word or *.private.txt is left.
  2. git commit: the shared pre-commit checks (gitleaks on staged files, shellcheck).
  3. The NUC (and so Gitea) must hold the commit first.
  4. gitleaks over the whole history.
OUTCOME 2026-11-17: written for to-do T1010, the day the public repo was created.
#>
$ErrorActionPreference = 'Continue'
$repo = 'D:\scratchpad-public'
$log = Join-Path $repo '.git\daily-push.log'
$gitleaks = 'C:\Users\USER\.local\share\checks\bin\gitleaks.exe'
function Say($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm') $m" | Add-Content -Path $log -Encoding utf8 }

$out = & py -3.14 -I D:\scratchpad\tools\public_copy.py --write 2>&1
if ($LASTEXITCODE -ne 0) { Say "copy: STOPPED, nothing pushed: $($out | Select-Object -Last 1)"; exit 1 }

Set-Location $repo
if (git status --porcelain) {
    git add -A
    git commit -q -m "Refresh from the Scratchpad $(Get-Date -Format 'yyyy-MM-dd HH:mm')" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Say 'commit: checks failed, nothing pushed (run git commit by hand to see why)'; exit 1 }
    Say "commit: $(git log --oneline -1)"
}
$want = git rev-parse main
foreach ($try in 1..3) {
    git push -q nuc main 2>&1 | Out-Null
    $have = (git ls-remote nuc refs/heads/main) -split '\s' | Select-Object -First 1
    if ($have -eq $want) { break }
    Start-Sleep -Seconds 10
}
if ($have -ne $want) { Say 'gitea: the NUC does not have this commit; GitHub skipped so Gitea stays first'; exit 1 }
Say 'gitea: sent to the NUC'

& $gitleaks git --redact --no-banner --exit-code 1 $repo 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Say 'github: gitleaks found something in the history; NOT pushed'; exit 1 }

$remote = (git ls-remote github refs/heads/main) -split '\s' | Select-Object -First 1
if ($want -eq $remote) { Say 'github: up to date'; exit 0 }
git push -q github main 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Say 'github: push failed'; exit 1 }
Say "github: pushed $($want.Substring(0,7))"
