$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { throw "Run scripts\windows\setup.ps1 first." }

$TaskName = "M.Y. Business Hotel HR - Monthly Report"
$Action = New-ScheduledTaskAction -Execute $VenvPython -Argument "-m scripts.generate_monthly_report --force" -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Monthly -DaysOfMonth 1 -At 9:00AM
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "Generate the previous month's local HR Excel and PDF reports on day 1 at 09:00." -Force | Out-Null

Write-Host "Monthly HR report task registered: day 1 at 09:00 local Windows time." -ForegroundColor Green
Write-Host "The task runs when this Windows user is logged on; missed runs start when available."
