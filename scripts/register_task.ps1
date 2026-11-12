<#
One-off setup: registers the daily scheduled refresh in Windows Task Scheduler.

Run once from the repo root in PowerShell:
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_task.ps1

Optional: -Time "18:30" to change the daily run time (default 18:00).
To remove it later:  Unregister-ScheduledTask -TaskName "WaitingTimesRefresh" -Confirm:$false
#>
param([string]$Time = "18:00")

$Script = Join-Path $PSScriptRoot "scheduled_refresh.ps1"
$RepoRoot = Split-Path -Parent $PSScriptRoot

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`"" `
    -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew
# Runs as you, only while you're logged on, so it can use your saved git and gh sign-ins.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "WaitingTimesRefresh" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Fetches new NHS cancer + RTT data, commits, pushes and publishes the waiting-times dashboards." `
    -Force | Out-Null

Write-Host "Registered 'WaitingTimesRefresh': daily at $Time (or at next logon/wake if missed)."
Write-Host "Test it now with:  Start-ScheduledTask -TaskName WaitingTimesRefresh"
