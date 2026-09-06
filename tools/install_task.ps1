# Registers (or removes) the per-user Task Scheduler entry that runs deck-dash at logon.
#
#   powershell -ExecutionPolicy Bypass -File tools\install_task.ps1          # register and start
#   powershell -ExecutionPolicy Bypass -File tools\install_task.ps1 -Remove  # unregister
#   powershell -ExecutionPolicy Bypass -File tools\install_task.ps1 -Status  # show task state
#
# Run it from your own terminal, not from a sandboxed shell: the check for hidapi.dll must
# see the real file system. No admin rights are needed for a task in the user's own session.
# ASCII only in this file (PowerShell 5.1 reads BOM-less scripts as ANSI).

param(
    [switch]$Remove,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'
$taskName = 'deck-dash'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = Join-Path $root '.venv\Scripts\pythonw.exe'

if ($Status) {
    $t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $t) { Write-Host "task '$taskName' is not registered"; exit 0 }
    $t | Get-ScheduledTaskInfo | Format-List TaskName, LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns
    Write-Host ("state: " + $t.State)
    exit 0
}

if ($Remove) {
    $t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $t) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "removed task '$taskName'"
    } else {
        Write-Host "task '$taskName' was not registered"
    }
    exit 0
}

# --- preflight -------------------------------------------------------------------------
if (-not (Test-Path $python)) { throw "missing $python (create the venv first)" }
$cfg = Get-Content (Join-Path $root 'config.toml') | Where-Object { $_ -match '^\s*hidapi_dir\s*=' }
$hidDir = ($cfg -replace '^\s*hidapi_dir\s*=\s*"([^"]*)".*$', '$1')
if (-not $hidDir) { throw 'hidapi_dir not found in config.toml' }
$dll = Join-Path $hidDir 'hidapi.dll'
if (-not (Test-Path $dll)) {
    throw "hidapi.dll not found at $dll as seen from this shell. If a sandboxed shell installed it, the copy may have been virtualized; place the real file there and rerun."
}
Write-Host ("hidapi.dll : " + (Get-Item $dll).Length + " bytes at " + $dll)
Write-Host ("python     : " + $python)
Write-Host ("workdir    : " + $root)

# --- task definition -------------------------------------------------------------------
$action = New-ScheduledTaskAction -Execute $python -Argument '-m deckdash' -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT20S'   # let the USB stack and the network settle after logon
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 99 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$desc = 'deck-dash: Stream Deck ambient display (restarts on failure; logs in logs\deckdash.log)'

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $desc -Force | Out-Null
Write-Host "registered task '$taskName' (at logon, restart every 1 min on failure, up to 99 times)"

# Stop any copy started by hand so the task's instance owns the device.
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*-m deckdash*' } | ForEach-Object {
    Write-Host ("stopping pid " + $_.ProcessId + " (" + $_.CommandLine + ")")
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
$t = Get-ScheduledTask -TaskName $taskName
Write-Host ("state: " + $t.State)
