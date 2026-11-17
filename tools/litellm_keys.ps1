<#
  litellm_keys.ps1 -- give each person/program its own LiteLLM key (models + the site_tools MCP tools),
  stored straight into Proton Pass, never printed (2026-11-13, Claude).

  Jason, 11-13: an MCP server "for Casey and Robin to integrate with the services ... to work remotely",
  and integrated into LiteLLM and n8n. Remote = https://llm.home.example.net (Caddy, 443, keys
  required on every route). Own keys instead of Jason's master key: each can be capped, traced and
  revoked alone.

    powershell -File litellm_keys.ps1                 create any missing keys (skips ones already in Pass)

  Each key: alias <name>, budget $5 per 30 days (raise it in the LiteLLM UI), MCP server site_tools.
  Pass item: vault "API keys", title "LiteLLM key - <name>", login field password = the key.
  Use: MCP endpoint https://llm.home.example.net/mcp/ (inside Docker: http://litellm:4000/mcp/)
       header  Authorization: Bearer <key>
#>
$ErrorActionPreference = 'Continue'
$env:PROTON_PASS_KEY_PROVIDER = 'fs'
$People = @('casey', 'robin', 'n8n')
$master = ("$(pass-cli item view --vault-name 'API keys' --item-title 'ai-stack LITELLM_MASTER_KEY' --field password 2>$null)").Trim()
if ($master.Length -lt 20) { Write-Output 'FAIL could not read the LiteLLM master key from Pass'; exit 1 }
foreach ($p in $People) {
  $title = "LiteLLM key - $p"
  if (pass-cli item view --vault-name 'API keys' --item-title $title --field username 2>$null) { Write-Output "have  $title"; continue }
  $body = @{ key_alias = $p; max_budget = 5; budget_duration = '30d'; metadata = @{ created_by = 'litellm_keys.ps1'; date = (Get-Date -Format s) }
             object_permission = @{ mcp_servers = @('site_tools') } } | ConvertTo-Json -Depth 4
  try {
    $r = Invoke-RestMethod -Uri 'http://127.0.0.1:4000/key/generate' -Method Post -Body $body -ContentType 'application/json' -Headers @{ Authorization = "Bearer $master" }
  } catch { Write-Output "FAIL $p : $($_.Exception.Message)"; continue }
  pass-cli item create login --vault-name 'API keys' --title $title --username $p --password $r.key --url 'https://llm.home.example.net' | Out-Null
  $r = $null
  Write-Output "made  $title"
}
$master = $null
