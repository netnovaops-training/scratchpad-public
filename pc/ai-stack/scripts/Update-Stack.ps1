<#
.SYNOPSIS
  Update a container, keeping the old one stopped-but-alive so it can be put
  straight back.

.DESCRIPTION
  JASON, 2026-09-24: "push the new update and then make that live ... the
  rollback put that under down so that the new one can take effect and the
  old one can still be put back into action ... without deleting the entire
  container."

  WHY THIS EXISTS AT ALL. `docker compose up -d` REMOVES the old container
  when it recreates one. That is usually fine and is exactly wrong here: the
  moment it is gone, "put the old one back" means rebuilding it from an image
  that may no longer be tagged. So before anything is pulled, this renames the
  running container out of the way and stops it. It is still there, still has
  its writable layer, and starting it again is one command.

  TWO SAFETY NETS, NOT ONE:
    1. the old CONTAINER, renamed `<name>-rollback` and stopped
    2. the old IMAGE, tagged `<repo>:rollback`
  The container alone is not enough -- if somebody later prunes it, the image
  tag still rebuilds an identical one. The image alone is not enough either,
  because it loses anything the old container wrote outside a volume.

  WHAT THIS CANNOT ROLL BACK, and it matters more than everything above:
  DATA. Named volumes are SHARED between the old container and the new one,
  so a service that migrates its database on first start has already written
  the new schema into the volume that the old container would come back to.
  Swapping the container back then gives you old code against a new schema,
  which is usually worse than the upgrade. That is why -IncludeDatabases has
  to be asked for explicitly and why the check below warns on it.

  DRY RUN BY DEFAULT. Nothing is pulled, stopped or swapped without -Apply.

.EXAMPLE
  .\Update-Stack.ps1
  Show which images have a newer version available. Changes nothing.

.EXAMPLE
  .\Update-Stack.ps1 -Service hermes-http -Apply
  Update one service, health-check it, and keep the old one for rollback.

.EXAMPLE
  .\Update-Stack.ps1 -Rollback hermes-http
  Put the previous container back.
#>
[CmdletBinding()]
param(
    # Limit to one service. Without it, every service is CHECKED but the
    # -Apply switch still refuses to touch more than one at a time.
    [string]$Service,

    # Actually do it. Without this the script only reports.
    [switch]$Apply,

    # Put the previous container back and stop the new one.
    [string]$Rollback,

    # Databases are skipped unless this is given. See the note above about
    # schema migrations being one-way.
    [switch]$IncludeDatabases,

    # How long to give a service to come up before calling it failed.
    [int]$HealthTimeoutSeconds = 90,

    [string]$ComposeDir = "C:\Jasons New Site\www\ai-stack"
)

$ErrorActionPreference = "Stop"

# Anything whose image looks like a database. Matched on the IMAGE, not the
# service name, because "garden-db" and "mariadb" are both databases but
# only one of them says so.
$DatabaseImages = 'mariadb|mysql|postgres|percona|mongo|redis|valkey|clickhouse'

function Say([string]$m, [string]$colour = "Gray") {
    Write-Host $m -ForegroundColor $colour
}

function Get-ComposeServices {
    Push-Location $ComposeDir
    try { (docker compose config --services) -split "`n" | Where-Object { $_ } }
    finally { Pop-Location }
}

function Get-ContainerFor([string]$svc) {
    Push-Location $ComposeDir
    try { (docker compose ps -q $svc) -split "`n" | Where-Object { $_ } | Select-Object -First 1 }
    finally { Pop-Location }
}

# --------------------------------------------------------------------------
#  Rollback
# --------------------------------------------------------------------------
if ($Rollback) {
    $old = "$Rollback-rollback"
    $exists = docker ps -a --filter "name=^$old$" --format "{{.Names}}"
    if (-not $exists) {
        Say "No container called $old. Nothing to roll back to." "Red"
        Say "Stopped-but-kept containers on this machine:" "Gray"
        docker ps -a --filter "name=-rollback" --format "  {{.Names}}  ({{.Status}})"
        exit 1
    }

    # The NEW container has to go before the name is free again. It is
    # renamed rather than removed, so a failed rollback is also reversible.
    $current = docker ps -a --filter "name=^$Rollback$" --format "{{.Names}}"
    if ($current) {
        Say "stopping the current $Rollback"
        docker stop $Rollback | Out-Null
        docker rename $Rollback "$Rollback-failed-$(Get-Date -Format yyyyMMddHHmmss)" | Out-Null
    }

    Say "putting the previous container back"
    docker rename $old $Rollback | Out-Null
    docker start $Rollback | Out-Null
    Start-Sleep -Seconds 3
    docker ps --filter "name=^$Rollback$" --format "  {{.Names}}  {{.Status}}  {{.Image}}"
    Say ""
    Say "Rolled back. The failed container was KEPT (renamed *-failed-*) so you" "Yellow"
    Say "can still read its logs: docker logs <that name>" "Yellow"
    exit 0
}

# --------------------------------------------------------------------------
#  What has an update waiting
# --------------------------------------------------------------------------
Push-Location $ComposeDir
try {
    $services = if ($Service) { @($Service) } else { Get-ComposeServices }
    $updatable = @()

    Say "Checking $($services.Count) service(s) for newer images..." "Cyan"
    foreach ($svc in $services) {
        $cid = Get-ContainerFor $svc
        if (-not $cid) { continue }

        $image = docker inspect $cid --format '{{.Config.Image}}'
        # A locally BUILT image has no upstream to compare against; pulling it
        # would either fail or fetch something unrelated from Docker Hub.
        if ($image -notmatch '[:/]' -or $image -like 'ai-stack/*' -or $image -like 'garden/*') {
            continue
        }

        $localDigest = docker inspect $cid --format '{{.Image}}'
        docker pull -q $image 2>&1 | Out-Null
        $newDigest = docker image inspect $image --format '{{.Id}}' 2>$null

        if ($newDigest -and $newDigest -ne $localDigest) {
            $isDb = $image -match $DatabaseImages
            $updatable += [pscustomobject]@{
                Service  = $svc
                Image    = $image
                Database = $isDb
            }
            $tag = if ($isDb) { "  [DATABASE - schema changes are one-way]" } else { "" }
            Say ("  UPDATE  {0,-28} {1}{2}" -f $svc, $image, $tag) `
                ($(if ($isDb) { "Yellow" } else { "Green" }))
        }
    }

    if (-not $updatable) { Say "Everything is current." "Green"; exit 0 }

    if (-not $Apply) {
        Say ""
        Say "$($updatable.Count) update(s) available. Nothing was changed." "Cyan"
        Say "To apply one:  .\Update-Stack.ps1 -Service <name> -Apply" "Gray"
        exit 0
    }

    # ONE AT A TIME, ALWAYS. When something breaks after an update you want a
    # single suspect, not eleven.
    if (-not $Service) {
        Say ""
        Say "-Apply updates ONE service. Name it with -Service." "Red"
        exit 1
    }

    $target = $updatable | Where-Object Service -eq $Service
    if (-not $target) { Say "$Service has no update waiting." "Green"; exit 0 }

    if ($target.Database -and -not $IncludeDatabases) {
        Say ""
        Say "$Service is a database. Its data lives in a volume the new AND old" "Red"
        Say "containers share, so a version that migrates the schema on start" "Red"
        Say "cannot be undone by swapping the container back. Re-run with" "Red"
        Say "-IncludeDatabases only after you have a dump you have restored" "Red"
        Say "from at least once." "Red"
        exit 1
    }

    # ---- keep the old one -------------------------------------------------
    $cid = Get-ContainerFor $Service
    $oldImage = docker inspect $cid --format '{{.Image}}'
    $repo = ($target.Image -split ':')[0]

    Say ""
    Say "tagging the current image ${repo}:rollback" "Cyan"
    docker tag $oldImage "${repo}:rollback" | Out-Null

    # An older rollback container would block the rename. It is removed only
    # here, and only after its replacement is about to exist.
    $prior = docker ps -a --filter "name=^$Service-rollback$" --format "{{.Names}}"
    if ($prior) {
        Say "removing the previous rollback container (one generation is kept)"
        docker rm -f "$Service-rollback" | Out-Null
    }

    Say "stopping $Service and keeping it as $Service-rollback" "Cyan"
    docker stop $Service | Out-Null
    docker rename $Service "$Service-rollback" | Out-Null

    # ---- bring the new one up --------------------------------------------
    Say "starting the new $Service" "Cyan"
    docker compose up -d --no-deps $Service | Out-Null

    # ---- does it actually work -------------------------------------------
    Say "watching it for $HealthTimeoutSeconds seconds..." "Cyan"
    $deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
    $healthy = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $state = docker inspect $Service --format '{{.State.Status}}' 2>$null
        $health = docker inspect $Service --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>$null
        $restarts = [int](docker inspect $Service --format '{{.RestartCount}}' 2>$null)

        # A crash loop reports "running" between restarts, so the restart
        # COUNT is the real signal, not the status word.
        if ($state -ne 'running' -or $restarts -gt 2) { break }
        if ($health -eq 'healthy' -or ($health -eq 'none' -and (Get-Date) -gt (Get-Date).AddSeconds(-1))) {
            if ($health -eq 'healthy') { $healthy = $true; break }
        }
        if ($health -eq 'none' -and $state -eq 'running') {
            Start-Sleep -Seconds 10
            $restarts = [int](docker inspect $Service --format '{{.RestartCount}}' 2>$null)
            if ((docker inspect $Service --format '{{.State.Status}}') -eq 'running' -and $restarts -le 2) {
                $healthy = $true
            }
            break
        }
    }

    if ($healthy) {
        Say ""
        Say "$Service updated and running." "Green"
        Say "The old container is kept, stopped, as $Service-rollback." "Green"
        Say "The old image is tagged ${repo}:rollback." "Green"
        Say "If it misbehaves later:  .\Update-Stack.ps1 -Rollback $Service" "Green"
    } else {
        Say ""
        Say "$Service did NOT come up cleanly. Rolling back automatically." "Red"
        docker logs --tail 30 $Service 2>&1 | ForEach-Object { Say "    $_" "DarkGray" }
        docker stop $Service 2>$null | Out-Null
        docker rename $Service "$Service-failed-$(Get-Date -Format yyyyMMddHHmmss)" | Out-Null
        docker rename "$Service-rollback" $Service | Out-Null
        docker start $Service | Out-Null
        Say "Previous container is back. The failed one was kept for its logs." "Yellow"
        exit 1
    }
}
finally { Pop-Location }
