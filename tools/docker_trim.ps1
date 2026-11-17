<#
  docker_trim.ps1 -- back up, then remove Docker leftovers on the PC (2026-11-13, Claude).

  Jason, 11-13: "lets trim old docker containers making backups to d drive ... making docker more efficient".

    powershell -File docker_trim.ps1            dry run: prints the plan, changes nothing
    powershell -File docker_trim.ps1 -Apply     backs up, then removes

  What goes, and what is kept first (everything lands in D:\docker-backups\<date>\):
    1. Stopped containers listed in $Containers: `docker inspect` JSON saved, then removed.
       Their bind-mounted data stays where it is on disk (jg-db -> D:\Garden for all\_local-data\db).
    2. Volumes no container uses:
       - build caches (Rust target/cargo/registry/rustup, trivy): removed, no backup (rebuilt on demand)
       - everything else is data: tar.gz to volumes\, and the volume is removed ONLY if the archive
         was written and is readable (tar -tzf). A failed backup leaves the volume alone.
    3. Images in $Images: locally built ones are `docker save`d first; public ones are only recorded
       (name + digest in manifest.csv) because they can be pulled again.
    4. Build cache older than 7 days.
  Kept on purpose: running containers and their volumes, compose-declared volumes, Docker Desktop's
  own Kubernetes images (kind/envoy; Kubernetes is switched on), base images used by scripts
  (python:3.12-alpine for index-feed.ps1, rust:1.99 for builds, shellcheck, busybox, alpine).
  Log: <backup dir>\trim.log ; one summary line to D:\joblog\events.jsonl.
#>
param([switch]$Apply)
$ErrorActionPreference = 'Continue'
$day = Get-Date -Format 'yyyy-MM-dd'
$bk = "D:\docker-backups\$day"
$Containers = @('voice-turbo-cpu', 'jg-db', 'backlog-sync', 'budget-tracker-prod-frontend-1', 'budget-tracker-prod-backend-1',
                'budget-tracker-prod-db-1', 'budget-tracker-prod-currency-rates-api-1', 'budget-tracker-prod-redis-1')
$Images = @('ai-stack/hermes:test', 'fp-runner:latest', 'letehaha/budget-tracker-be:latest', 'letehaha/budget-tracker-fe:latest',
            'letehaha/currency-rates-api:latest', 'postgres:16', 'redis:7', 'mariadb:lts-noble')
$Cache = '(?i)target|cargo|registry|-reg$|rustup|trivy'
$Declared = @(Push-Location 'C:\Jasons New Site\www\ai-stack'; docker compose config --volumes 2>$null | ForEach-Object { "ai-stack_$_" }; Pop-Location)

New-Item -ItemType Directory -Force "$bk\containers", "$bk\volumes", "$bk\images" | Out-Null
$log = "$bk\trim.log"
function Say($m) { $m | Tee-Object -FilePath $log -Append }
function Act($what, [scriptblock]$do) { if ($Apply) { & $do 2>&1 | Out-File $log -Append; Say "done  $what" } else { Say "plan  $what" } }
"ref,kind,action,detail" | Set-Content "$bk\manifest.csv"
function Note($ref, $kind, $action, $detail) { "`"$ref`",$kind,$action,`"$detail`"" | Add-Content "$bk\manifest.csv" }

Say "== $(Get-Date -Format s) docker_trim $(if ($Apply) {'APPLY'} else {'dry run'})"
# 1. containers
foreach ($c in $Containers) {
  $state = docker inspect -f '{{.State.Status}}' $c 2>$null
  if (-not $state) { continue }
  if ($state -eq 'running') { Say "skip  $c is running"; continue }
  docker inspect $c | Set-Content "$bk\containers\$c.json"
  Note $c container remove "state=$state; inspect saved"
  Act "remove container $c" { docker rm $c }
}
# 2. unused volumes (re-listed after the containers are gone, so their volumes show up here)
# dangling = no container (running or stopped) references it. First draft parsed `docker ps
# --format {{.Mounts}}`, which truncates long names: its dry run listed live volumes such as
# ai-stack_caddy_data as unused. Docker's own filter is the only trusted source.
$vols = docker volume ls -q --filter dangling=true | Where-Object { $_ -notin $Declared }
$failed = 0
foreach ($v in $vols) {
  if ($v -match $Cache) { Note $v cache remove 'rebuildable'; Act "remove cache volume $v" { docker volume rm $v }; continue }
  # A volume with mount options (nfs/cifs) is a doorway to a share, not data: pc2linux-nas points at the
  # whole NAS "Computer backup" share, so a tar would copy terabytes. Keep its definition, never back it up.
  $opts = docker volume inspect -f '{{json .Options}}' $v
  if ($opts -match '"type"') { Note $v network kept "$opts"; Say "skip  $v is a network mount"; continue }
  $tgz = "$bk\volumes\$v.tar.gz"
  if (-not $Apply) { Say "plan  back up + remove data volume $v"; continue }
  docker run --rm -v "${v}:/v:ro" -v "$bk\volumes:/out" alpine:3 sh -c "tar czf /out/$v.tar.gz -C /v . && tar tzf /out/$v.tar.gz >/dev/null" 2>&1 | Out-File $log -Append
  if ($LASTEXITCODE -eq 0 -and (Test-Path $tgz)) {
    Note $v data remove "backup $([math]::Round((Get-Item $tgz).Length/1MB,1)) MB"
    Act "remove data volume $v (backed up)" { docker volume rm $v }
  } else { $failed++; Note $v data KEPT 'backup failed'; Say "KEPT  $v - backup failed" }
}
# 3. images
foreach ($i in $Images) {
  $id = docker image inspect -f '{{.Id}}' $i 2>$null
  if (-not $id) { continue }
  if (docker ps -a --filter "ancestor=$i" -q) { Say "skip  image $i still used"; continue }
  $digest = (docker image inspect -f '{{join .RepoDigests " "}}' $i 2>$null)
  if ($i -match '^(ai-stack/|fp-runner)' -and $Apply) {
    $file = "$bk\images\$($i -replace '[/:]','_').tar"
    docker save -o $file $i 2>&1 | Out-File $log -Append
    if ($LASTEXITCODE -ne 0) { $failed++; Say "KEPT  image $i - save failed"; continue }
    Note $i image remove "saved to images\"
  } else { Note $i image remove "public; pull again: $digest" }
  Act "remove image $i" { docker rmi $i }
}
# 4. build cache
Act 'prune build cache older than 7 days' { docker builder prune -f --filter 'until=168h' }

$line = @{ ts = (Get-Date).ToString('s'); job = 'docker_trim'; ok = ($failed -eq 0); detail = "$(if ($Apply) {'apply'} else {'dry run'}); failed=$failed; backups=$bk" } | ConvertTo-Json -Compress
if ($Apply) { Add-Content 'D:\joblog\events.jsonl' $line }
Say "$(if ($failed) {'FAIL'} else {'PASS'}) docker_trim: $failed failures. Plan/manifest: $bk"
