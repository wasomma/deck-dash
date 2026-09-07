# Registers (or removes) the per-user Task Scheduler entry that runs deck-dash at logon.
#
#   powershell -ExecutionPolicy Bypass -File tools\install_task.ps1          # register and start
#   powershell -ExecutionPolicy Bypass -File tools\install_task.ps1 -Remove  # unregister
#   powershell -ExecutionPolicy Bypass -File tools\install_task.ps1 -Status  # show task state
#   powershell -ExecutionPolicy Bypass -File tools\install_task.ps1 -Stop    # stop the running copy (e.g. before tools\calibrate.py)
#   powershell -ExecutionPolicy Bypass -File tools\install_task.ps1 -Start   # start it again
#   deckdash ctl restart   (from the venv)                                    # -Stop then -Start, detached
#
# Run it from your own terminal, not from a sandboxed shell: the check for hidapi.dll must
# see the real file system. No admin rights are needed for a task in the user's own session.
# ASCII only in this file (PowerShell 5.1 reads BOM-less scripts as ANSI).

param(
    [switch]$Remove,
    [switch]$Status,
    [switch]$Stop,
    [switch]$Start
)

$ErrorActionPreference = 'Stop'
$taskName = 'deck-dash'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = Join-Path $root '.venv\Scripts\pythonw.exe'
$launcher = Join-Path $root '.venv\Scripts\deckdashw.exe'   # from 'pip install -e .' (Phase 7a); pythonw -m deckdash until then

function Stop-DeckDashProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        # Only copies that own the Stream Deck. 'ctl' clients are short-lived, and a '--sim' run
        # has its own pipe and no device, so it is meant to run beside the live app: leave both.
        ($_.Name -like 'python*' -and ($_.CommandLine -like '*-m deckdash*' -or $_.CommandLine -like '*\deckdashw.exe*' -or $_.CommandLine -like '*\deckdash.exe*') -and $_.CommandLine -notlike '* ctl *' -and $_.CommandLine -notlike '*--sim*') -or ($_.CommandLine -like '*media_watch.ps1*' -and $_.Name -like 'powershell*')
    } | ForEach-Object {
        Write-Host ("stopping pid " + $_.ProcessId + " (" + $_.Name + ")")
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

if ($Stop) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Stop-DeckDashProcesses
    Write-Host 'deck-dash stopped'
    exit 0
}

if ($Start) {
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 2
    Write-Host ("state: " + (Get-ScheduledTask -TaskName $taskName).State)
    exit 0
}

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
if (Test-Path $launcher) { $exe = $launcher; $exeArgs = '' } else { $exe = $python; $exeArgs = '-m deckdash' }
$cfg = Get-Content (Join-Path $root 'config.toml') | Where-Object { $_ -match '^\s*hidapi_dir\s*=' }
$hidDir = ($cfg -replace '^\s*hidapi_dir\s*=\s*"([^"]*)".*$', '$1')
if (-not $hidDir) { throw 'hidapi_dir not found in config.toml' }
$dll = Join-Path $hidDir 'hidapi.dll'
if (-not (Test-Path $dll)) {
    throw "hidapi.dll not found at $dll as seen from this shell. If a sandboxed shell installed it, the copy may have been virtualized; place the real file there and rerun."
}
Write-Host ("hidapi.dll : " + (Get-Item $dll).Length + " bytes at " + $dll)
Write-Host ("launcher   : " + $exe + " " + $exeArgs)
Write-Host ("workdir    : " + $root)

# --- task definition -------------------------------------------------------------------
if ($exeArgs) {
    $action = New-ScheduledTaskAction -Execute $exe -Argument $exeArgs -WorkingDirectory $root
} else {
    $action = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $root
}
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT20S'   # let the USB stack and the network settle after logon
# Priority 5 = NORMAL_PRIORITY_CLASS. The default 7 (below normal) starved the render loop:
# flush max 150-320 ms once a minute on every scene; 66-72 ms at normal (Phase 6, 2026-09-06).
$settings = New-ScheduledTaskSettingsSet `
    -Priority 5 `
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
Stop-DeckDashProcesses
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
$t = Get-ScheduledTask -TaskName $taskName
Write-Host ("state: " + $t.State)
