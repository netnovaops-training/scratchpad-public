<#
daily_github_push.ps1 - once a night: commit the day's scratchpad changes, send them to Gitea, and push to
GitHub only if a full gitleaks scan of the history is clean.

How to run:   powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\daily_github_push.ps1
Scheduled:    Windows task "Scratchpad daily GitHub push", 16:30 every day (made 2026-11-16 at 23:30; moved
              to 16:30 on 11-17 so GitHub checks and Dependabot see the day's work before the 03:00 merge).
Reads/writes: D:\scratchpad (git), remotes nuc (-> Gitea via the NUC's post-receive hook) and github
              (github.com/netnovaops-training/Scratchpad, private). Log: D:\scratchpad\.git\daily-push.log
How to test:  run it by hand; the log ends with "github: pushed <sha>" or "github: up to date".

Order is the rule Jason set 11-16: Gitea gets everything first, GitHub gets it once a day.
The commit itself goes through the normal pre-commit checks (gitleaks on staged files, shellcheck); if they
fail, nothing is committed or pushed and the log says so.
OUTCOME 2026-11-16: written and run by hand once on the first day; see the log.
#>
$ErrorActionPreference = 'Continue'
$repo = 'D:\scratchpad'
$log = Join-Path $repo '.git\daily-push.log'
$gitleaks = 'C:\Users\USER\.local\share\checks\bin\gitleaks.exe'
function Say($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm') $m" | Add-Content -Path $log -Encoding utf8 }

Set-Location $repo
if (git status --porcelain) {
    git add -A
    git commit -q -m "Daily snapshot $(Get-Date -Format 'yyyy-MM-dd')" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Say 'commit: checks failed, nothing pushed (run git commit by hand to see why)'; exit 1 }
    Say "commit: $(git log --oneline -1)"
}
# The post-commit hook pushes to the NUC in the background at the same moment, so a failed push here can
# just mean "already sent". Judge by the result: the NUC must hold this commit (OUTCOME 11-16: first run
# lost exactly that race).
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

$local = git rev-parse main
$remote = (git ls-remote github refs/heads/main) -split '\s' | Select-Object -First 1
if ($local -eq $remote) { Say 'github: up to date'; exit 0 }
git push -q github main 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Say 'github: push failed'; exit 1 }
Say "github: pushed $($local.Substring(0,7))"
