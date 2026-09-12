# Registers "NYCFreeEvents" to run DAILY (8:00 AM), SILENTLY, via Task Scheduler
# — a morning Telegram digest of free / almost-free things to do in NYC.
# Silent = launched with pythonw.exe (no console window).
# Run this ONCE:  .\register_task.ps1   (elevate if it reports an access error)
# Re-running updates the existing task.

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$scout     = Join-Path $scriptDir "nyc_free_events.py"

# Prefer pythonw.exe (windowless). Fall back to python.exe if not found.
$python = (Get-Command python).Source
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
$exe = if (Test-Path $pythonw) { $pythonw } else { $python }

$action = New-ScheduledTaskAction -Execute $exe -Argument "`"$scout`"" -WorkingDirectory $scriptDir

# Every day at 8:00 AM.
$trigger = New-ScheduledTaskTrigger -Daily -At 8:00AM

# Start when available (catch up if the PC was asleep), run on battery (critical
# for laptops: default power conditions leave the task stuck "Queued"), hidden.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopOnIdleEnd -RunOnlyIfNetworkAvailable -Hidden `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew

# Run as the current user, only when logged on (no stored password needed).
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "NYCFreeEvents" `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Daily 8AM digest of free/almost-free NYC things to do (food, wine, beauty, outdoors, museums, concerts, cultural) from live RSS feeds, sent to Telegram. Runs silently." `
    -Force | Out-Null

Write-Host "Registered 'NYCFreeEvents' (daily 8:00 AM, silent via $([System.IO.Path]::GetFileName($exe)))."
Write-Host "Manage it in Task Scheduler, or run:  Get-ScheduledTask NYCFreeEvents | Get-ScheduledTaskInfo"
Write-Host "Test now (console, no Telegram):  python `"$scout`" --preview"
Write-Host "Test now (real Telegram send):    python `"$scout`""
