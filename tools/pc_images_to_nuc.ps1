# pc_images_to_nuc.ps1 - send the PC's running containers to the NUC for the Tuesday update email (2026-11-18).
# Windows task "Container list to NUC", Tuesday 09:30. Writes name<TAB>image<TAB>RepoDigests-json per line to
# ~/nuc-homelab/audit/pc-images.tsv on the NUC (weekly_updates.py uses it if under 2 days old). Read-only.
# OUTCOME: (2026-11-18) first version.
$ErrorActionPreference = 'Stop'
$lines = foreach ($row in (docker ps --format '{{.Names}}|{{.Image}}|{{.ID}}')) {
    $n, $i, $id = $row -split '\|'
    $img = docker inspect -f '{{.Image}}' $id
    $dig = docker image inspect -f '{{json .RepoDigests}}' $img
    "$n`t$i`t$dig"
}
$tmp = Join-Path $env:TEMP 'pc-images.tsv'
[IO.File]::WriteAllText($tmp, (($lines -join "`n") + "`n"))   # LF, no BOM: read on Linux
scp -q $tmp server@100.64.0.10:nuc-homelab/audit/pc-images.tsv
