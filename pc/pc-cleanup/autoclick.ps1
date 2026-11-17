<#
  autoclick.ps1 -- dot-source this. Jason, 2026-11-04: "make sure it clicks yes too ... also the
  next buttons and finish ... or done". It cannot (and must not) click Windows' own UAC prompt;
  it clicks only buttons inside windows that belong to the uninstaller WE started (or its child
  processes), and only these names. It never clicks Cancel/Back/No.
    Start-Clicked <exe> <args> [-TimeoutMin 10]   start it, click through it, wait for it to end
    Disable-QuickEdit                              a click in the console can't freeze the run
#>
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$script:Press = '^(&?Next|Next ?>|&?Finish|&?OK|&?Yes|Yes to &?all|&?Uninstall|&?Remove|&?Close|&?Done|&?Continue|&?Accept|I &?agree)$'
$script:Tick = '^(I &?accept.*|I agree.*|Remove (all )?(user )?(data|settings|files|preferences).*|Delete .*(data|settings|files).*|Yes,? remove.*|Completely remove.*)$'

function Get-Tree([int]$root) {
  $all = Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, Name
  $ids = [Collections.Generic.HashSet[int]]::new(); [void]$ids.Add($root); $grew = $true
  while ($grew) { $grew = $false; foreach ($p in $all) { if ($ids.Contains([int]$p.ParentProcessId) -and $ids.Add([int]$p.ProcessId)) { $grew = $true } } }
  # NSIS/Inno uninstallers re-launch themselves from %TEMP% under these names, detached from the tree
  foreach ($p in $all | Where-Object Name -match '^(Un_A|Au_|_iu14D2N|unins\d+|.*uninst.*)\.(exe|tmp)$') { [void]$ids.Add([int]$p.ProcessId) }
  $ids
}

function Click-Through([System.Collections.Generic.HashSet[int]]$ids) {
  $A = [System.Windows.Automation.AutomationElement]; $T = [System.Windows.Automation.TreeScope]
  $clicked = @()
  foreach ($w in $A::RootElement.FindAll($T::Children, [System.Windows.Automation.Condition]::TrueCondition)) {
    if (-not $ids.Contains($w.Current.ProcessId)) { continue }
    $all = $w.FindAll($T::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($e in $all) {      # tick "I accept" / "remove my data" first
      $n = $e.Current.Name; $ct = $e.Current.ControlType.ProgrammaticName
      if ($n -match $script:Tick -and $e.Current.IsEnabled) {
        try {
          if ($ct -match 'CheckBox') { $tp = $e.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern); if ($tp.Current.ToggleState -ne 'On') { $tp.Toggle(); $clicked += "tick:$n" } }
          elseif ($ct -match 'RadioButton') { $e.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select(); $clicked += "pick:$n" }
        } catch {}
      }
    }
    # Jason: "if it asks are you sure ... I don't want it, say no" -- a dialog that offers to KEEP
    # something (settings, presets, data, a backup) gets No; every other question gets the normal button.
    $text = ($all | ForEach-Object { $_.Current.Name }) -join ' '
    $keepQ = $text -match '(?i)\b(keep|preserve|retain|save (your |my )?(settings|data|presets|preferences|configuration))\b'
    $press = if ($keepQ) { '^&?No$' } else { $script:Press }
    foreach ($e in $all) {
      if ($e.Current.ControlType.ProgrammaticName -notmatch 'Button' -or -not $e.Current.IsEnabled) { continue }
      $n = ($e.Current.Name -replace '\s+', ' ').Trim()
      if ($n -match $press) {
        try { $e.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); $clicked += $n; break } catch {}
      }
    }
  }
  $clicked
}

function Start-Clicked([string]$exe, [string]$arguments = '', [int]$TimeoutMin = 10) {
  $p = Start-Process $exe $arguments -PassThru
  $until = (Get-Date).AddMinutes($TimeoutMin); $log = @()
  do {
    Start-Sleep -Milliseconds 1200
    $ids = Get-Tree $p.Id
    $log += Click-Through $ids
    $alive = @(Get-Process -Id ([int[]]@($ids)) -ErrorAction SilentlyContinue).Count -gt 0
  } while ($alive -and (Get-Date) -lt $until)
  [pscustomobject]@{ Exit = $(if ($p.HasExited) { $p.ExitCode } else { 'timeout' }); Clicked = ($log | Select-Object -Unique) -join ', ' }
}

function Disable-QuickEdit {
  Add-Type -Namespace W -Name C -MemberDefinition '[DllImport("kernel32.dll")] public static extern System.IntPtr GetStdHandle(int h);
    [DllImport("kernel32.dll")] public static extern bool GetConsoleMode(System.IntPtr h, out uint m);
    [DllImport("kernel32.dll")] public static extern bool SetConsoleMode(System.IntPtr h, uint m);' -ErrorAction SilentlyContinue
  $h = [W.C]::GetStdHandle(-10); $m = 0
  if ([W.C]::GetConsoleMode($h, [ref]$m)) { [void][W.C]::SetConsoleMode($h, ($m -band (-bnot 0x40)) -bor 0x80) }   # QuickEdit off
}
