# =============================================================================
#  Pull the workflows out of n8n and back into the folder.
#
#  WHY THIS IS NEEDED AT ALL
#  -------------------------
#  n8n keeps its workflows in its own database, inside a Docker volume. The
#  .json files in n8n\workflows are what you IMPORTED -- they are not what is
#  RUNNING. The moment you edit a node in the browser those two disagree, and
#  nothing tells you: the file on disk still looks right, and a rebuild that
#  re-imports it would quietly undo the change you made.
#
#  So this exports what is actually live, named by what the workflow is called
#  rather than by its id, and drops it in the folder. Run it after editing
#  anything in the browser and the folder becomes the truth again.
#
#  IT NEVER CHANGES n8n. Export only, one direction.
#
#      .\n8n-export.ps1            export everything
#      .\n8n-export.ps1 -ActiveOnly  skip the ones that are switched off
# =============================================================================
param(
    [switch]$ActiveOnly,
    [string]$Into = (Join-Path $PSScriptRoot "n8n\workflows")
)

$ErrorActionPreference = "Stop"

if (-not (docker ps --format "{{.Names}}" | Select-String -Quiet "^n8n$")) {
    Write-Host "n8n is not running. Start it first:" -ForegroundColor Yellow
    Write-Host "  docker compose -f docker-compose.yml -f docker-compose.web.yml --profile everything up -d n8n"
    exit 1
}

New-Item -ItemType Directory -Force -Path $Into | Out-Null

Write-Host "Exporting from n8n..." -ForegroundColor Cyan
# --separate gives one file per workflow. Errors about unknown credentials for
# nodes you do not use are normal and harmless -- n8n loads every node type it
# ships with, and some want credentials that were never configured.
docker exec n8n sh -c "rm -rf /tmp/wfexport && n8n export:workflow --all --separate --output=/tmp/wfexport" 2>&1 |
    Where-Object { $_ -notmatch "Failed to load Custom API options" } |
    ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

$staging = Join-Path $env:TEMP "n8n-export"
if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
docker cp n8n:/tmp/wfexport $staging | Out-Null

$seen = @{}
$written = 0
$skipped = 0
$dupes = @()

# ACTIVE FIRST. When two workflows share a name, the active one is the one that
# matters and must be the one that wins the filename -- otherwise you export a
# switched-off copy over the top of the live one and the folder now describes
# something that is not running.
$files = Get-ChildItem (Join-Path $staging "*.json") | ForEach-Object {
    $d = Get-Content $_.FullName -Raw | ConvertFrom-Json
    [pscustomobject]@{ Path = $_.FullName; Name = $d.name; Active = [bool]$d.active }
} | Sort-Object -Property @{Expression = "Active"; Descending = $true}, Name

foreach ($f in $files) {
    if ($ActiveOnly -and -not $f.Active) { $skipped++; continue }

    # A filename from a workflow name. Anything Windows dislikes becomes a dash.
    $slug = ($f.Name -replace '^ai-stack:\s*', '' -replace '[^A-Za-z0-9._-]+', '-').Trim('-').ToLower()
    if (-not $slug) { $slug = "workflow" }

    if ($seen.ContainsKey($slug)) {
        # A second workflow with the same name. Kept, but marked, because the
        # honest thing is to show you the mess rather than silently pick one.
        $seen[$slug]++
        $dupes += "$($f.Name)$(if ($f.Active) { ' (ACTIVE)' } else { ' (off)' })"
        $slug = "$slug.duplicate-$($seen[$slug])"
    } else {
        $seen[$slug] = 1
    }

    Copy-Item $f.Path (Join-Path $Into "$slug.json") -Force
    $state = if ($f.Active) { "active" } else { "off   " }
    Write-Host ("  {0}  {1}.json" -f $state, $slug)
    $written++
}

Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "$written written to $Into" -ForegroundColor Green
if ($skipped) { Write-Host "$skipped switched-off workflows skipped" -ForegroundColor DarkGray }

if ($dupes.Count) {
    Write-Host ""
    Write-Host "DUPLICATE NAMES -- more than one workflow is called the same thing:" -ForegroundColor Yellow
    $dupes | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    Write-Host ""
    Write-Host "  These are usually left behind by importing a file more than once." -ForegroundColor DarkGray
    Write-Host "  Only the ACTIVE one runs. Delete the rest in the browser, then run" -ForegroundColor DarkGray
    Write-Host "  this again and the .duplicate files will stop appearing." -ForegroundColor DarkGray
}
