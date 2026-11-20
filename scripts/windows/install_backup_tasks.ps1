param(
    [string]$DailyTime = "02:00",
    [string]$WeeklyTime = "02:30",
    [string]$QuarterlyTime = "03:00"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Runner = Join-Path $ProjectRoot "scripts\backup\run_backup.py"

if (-not (Test-Path $Python)) {
    throw "Python virtual environment not found at $Python. Run the hotel setup script first."
}
if (-not (Test-Path $Runner)) {
    throw "Backup runner not found at $Runner."
}

$Tasks = @(
    @{
        Name = "MY Business Hotel HR - Daily Backup"
        Schedule = @("/SC", "DAILY", "/ST", $DailyTime)
        Type = "daily"
    },
    @{
        Name = "MY Business Hotel HR - Weekly Backup"
        Schedule = @("/SC", "WEEKLY", "/D", "SUN", "/ST", $WeeklyTime)
        Type = "weekly"
    },
    @{
        Name = "MY Business Hotel HR - Quarterly Backup"
        Schedule = @("/SC", "MONTHLY", "/MO", "3", "/D", "1", "/ST", $QuarterlyTime)
        Type = "quarterly"
    }
)

foreach ($Task in $Tasks) {
    $TaskRun = '"' + $Python + '" "' + $Runner + '" --type ' + $Task.Type
    $Arguments = @(
        "/Create",
        "/TN", $Task.Name,
        "/TR", $TaskRun,
        "/RU", "SYSTEM",
        "/RL", "HIGHEST",
        "/F"
    ) + $Task.Schedule

    & schtasks.exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create scheduled task: $($Task.Name)"
    }
    Write-Host "Installed scheduled task: $($Task.Name)"
}

Write-Host "Backup tasks installed."
Write-Host "Daily retention: 40 days."
Write-Host "Weekly retention: 365 days."
Write-Host "Quarterly full backups: never auto-deleted."
Write-Host "Default schedule: daily $DailyTime, Sunday $WeeklyTime, first day of each quarter $QuarterlyTime."
Write-Host "Times can be changed by rerunning this script with -DailyTime, -WeeklyTime and -QuarterlyTime."
