# Ship the PC's searchable data to the NUC's index. Hourly.
#
# Jason, 2026-09-07: "I just want a universal recipe and workout searcher."
#
# WHY A FEED FILE RATHER THAN A LIVE CALL. The search service runs on the NUC,
# because that is where Meilisearch and the document vault are. recipe-app
# runs HERE, on the PC, with no published port -- so the NUC cannot ask it
# anything. There were three ways to close that gap:
#
#   1. Publish recipe-app to the LAN.
#   2. Add an IP-pinned nginx sidecar, the pattern already used for Paperless,
#      Gitea, Grafana and openGym.
#   3. Push the data over the ssh channel that already carries database dumps
#      to the vault twice a week.
#
# The third wins because it adds NO LISTENING SOCKET. A search feature nobody
# outside this house will ever use should not cost a new open port, and
# recipes change on the order of once a week -- there is nothing here that
# needs to be live to the second.
#
# WHAT IS SENT: titles, ingredients and steps. That is what a search box needs
# to match on. Photos are referenced by the URL the app already serves them
# from rather than copied, so this stays a small text file rather than a
# second copy of the recipe box.

param(
    # NOT ON THE VAULT. The vault is for what must survive this machine;
    # this feed is rebuilt from recipe-app every hour, so losing it costs
    # one hour and no data. It also keeps the write inside the NUC user's
    # own home, which needs no root to create.
    [string]$Dest    = "/home/user/index-feeds",
    [string]$NucHost = "server@192.168.1.86",
    [string]$Api     = "http://recipe-app:8004",
    [string]$AsUser  = "user1@example.com",
    [switch]$Install
)

$ErrorActionPreference = "Continue"
$log = Join-Path $PSScriptRoot "transcripts\index-feed.log"

function Say($m) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Write-Output $line
    Add-Content -Path $log -Value $line -Encoding utf8 -ErrorAction SilentlyContinue
}

if ($Install) {
    # Same .vbs launcher reasoning as db-export.ps1: -WindowStyle Hidden does
    # NOT stop the console flashing, because Task Scheduler starts a console
    # host before PowerShell ever reads the flag. Hourly, that would be a
    # black window blinking over whatever you are doing, all day.
    $vbs = Join-Path $PSScriptRoot "index-feed-launcher.vbs"
    @"
' Starts the feed with no console window. See index-feed.ps1 for why.
CreateObject("WScript.Shell").Run _
  "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""$PSScriptRoot\index-feed.ps1""", 0, False
"@ | Set-Content -Path $vbs -Encoding ascii

    $action  = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbs`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(7) `
                   -RepetitionInterval (New-TimeSpan -Hours 1)
    # NOT -WakeToRun, unlike the database export. Missing an hour of recipe
    # freshness costs nothing; waking the machine every hour to avoid it
    # would be a real cost for no benefit.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
    Register-ScheduledTask -TaskName "ai-stack index feed" -Action $action `
        -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Output "Scheduled: hourly."
    exit 0
}

Say "=== index feed starting ==="
$tmp = Join-Path $env:TEMP "indexfeed"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$outFile = Join-Path $tmp "recipes.json"

# Run the fetch INSIDE the compose network. recipe-app has no published port,
# which is the whole reason this script exists -- so reach it the way its
# neighbours do rather than opening one.
$py = @'
import json, os, sys, urllib.request

api = os.environ["API"]; who = os.environ["ASUSER"]
def get(path):
    r = urllib.request.Request(api + path, headers={"X-Auth-Email": who})
    with urllib.request.urlopen(r, timeout=30) as f:
        return json.load(f)

ids, page = [], 1
while True:
    d = get(f"/api/recipes?page={page}&per_page=100")
    got = d.get("recipes", [])
    if not got:
        break
    ids += [(r["id"], r.get("title",""), r.get("image",""), r.get("course",""))
            for r in got]
    if len(ids) >= int(d.get("total", 0)):
        break
    page += 1

out = []
for rid, title, image, course in ids:
    try:
        d = get(f"/api/recipes/{rid}")
    except Exception:
        # One unreadable recipe must not cost the other 248. It is recorded
        # from the listing alone rather than dropped, so it stays findable
        # by name even when its detail call fails.
        out.append({"id": rid, "title": title, "image": image,
                    "course": course, "ingredients": "", "steps": ""})
        continue
    out.append({
        "id": rid,
        "title": d.get("title") or title,
        "image": d.get("image") or image,
        "course": course,
        "ingredients": " ".join(
            i.get("original") or i.get("text") or "" for i in d.get("ingredients", [])),
        "steps": " ".join(s.get("text") or "" for s in d.get("steps", [])),
        "source": d.get("source") or "",
    })

json.dump({"generated": int(__import__("time").time()), "recipes": out},
          open("/out/recipes.json", "w", encoding="utf-8"))
print(len(out))
'@

$pyFile = Join-Path $tmp "fetch.py"
Set-Content -Path $pyFile -Value $py -Encoding utf8

# --mount rather than -v: the temp path has no spaces here, but -v silently
# creates a directory when it cannot parse a Windows path, and a silently
# empty feed is exactly the failure this script must not have.
$count = & docker run --rm --network ai-stack_default `
    -e API=$Api -e ASUSER=$AsUser `
    --mount "type=bind,source=$tmp,target=/out" `
    --entrypoint python python:3.12-alpine /out/fetch.py 2>&1 | Select-Object -Last 1

if (-not (Test-Path $outFile) -or (Get-Item $outFile).Length -lt 200) {
    Say "ERROR: no usable feed produced -- leaving the previous one in place"
    exit 1
}
Say "collected $count recipes ($((Get-Item $outFile).Length) bytes)"

# NO 2>$null ON NATIVE COMMANDS. In PowerShell 5.1 redirecting a native exe's
# stderr wraps each line in an ErrorRecord and sets $? to $false even on a
# clean exit -- which is what made db-export.ps1 report a failure while ssh
# was working perfectly. Only $LASTEXITCODE is trustworthy here.
& ssh -o BatchMode=yes -o ConnectTimeout=10 $NucHost "mkdir -p '$Dest'"
if ($LASTEXITCODE -ne 0) { Say "ERROR: could not reach the vault"; exit 1 }

# Written to a temporary name and moved into place on the far side, so the
# indexer can never read a half-copied file: the move is atomic, the copy is
# not.
& scp -q -o BatchMode=yes $outFile "${NucHost}:$Dest/.recipes.json.part"
if ($LASTEXITCODE -ne 0) { Say "ERROR: copy failed"; exit 1 }
& ssh -o BatchMode=yes $NucHost "mv '$Dest/.recipes.json.part' '$Dest/recipes.json'"
if ($LASTEXITCODE -ne 0) { Say "ERROR: could not put the feed in place"; exit 1 }

Say "=== feed delivered ==="
