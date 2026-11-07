$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot
Write-Host "M.Y. Business Hotel HR - Windows setup" -ForegroundColor Cyan
Write-Host "Local-only setup; no Windows auto-start is configured."
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw "Install Python 3.11+ with the Python Launcher (py)." }
& py -3.11 --version
if ($LASTEXITCODE -ne 0) { throw "Python 3.11 was not found." }
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create virtual environment." }
}
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not upgrade pip." }
& $VenvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw "Could not install dependencies." }
if (-not (Test-Path ".env")) {
    Write-Host "Enter PostgreSQL connection details." -ForegroundColor Yellow
    $DbHost = Read-Host "Database host (default 127.0.0.1)"
    if ([string]::IsNullOrWhiteSpace($DbHost)) { $DbHost = "127.0.0.1" }
    $DbPort = Read-Host "Database port (default 5432)"
    if ([string]::IsNullOrWhiteSpace($DbPort)) { $DbPort = "5432" }
    $DbName = Read-Host "Database name"
    $DbUser = Read-Host "Database username"
    $SecurePassword = Read-Host "Database password" -AsSecureString
    $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePassword)
    try { $DbPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer) }
    if ([string]::IsNullOrWhiteSpace($DbName) -or [string]::IsNullOrWhiteSpace($DbUser) -or [string]::IsNullOrEmpty($DbPassword)) {
        throw "Database name, username, and password are required."
    }
    $EncodedUser = [Uri]::EscapeDataString($DbUser)
    $EncodedPassword = [Uri]::EscapeDataString($DbPassword)
    $DatabaseUrl = "postgresql+psycopg://{0}:{1}@{2}:{3}/{4}" -f $EncodedUser, $EncodedPassword, $DbHost, $DbPort, $DbName
    $SessionSecret = (& $VenvPython -c "import secrets; print(secrets.token_urlsafe(48))").Trim()
    if ($LASTEXITCODE -ne 0 -or $SessionSecret.Length -lt 32) { throw "Could not generate session secret." }
    $EnvContent = @"
APP_NAME="M.Y. Business Hotel HR"
APP_ENV=development
APP_HOST=127.0.0.1
APP_PORT=8000
DATABASE_URL="$DatabaseUrl"
SESSION_SECRET="$SessionSecret"
"@
    [System.IO.File]::WriteAllText((Join-Path $ProjectRoot ".env"), $EnvContent, [System.Text.UTF8Encoding]::new($false))
    $DbPassword = $null
    $SecurePassword = $null
    Write-Host "Created .env. Keep it private." -ForegroundColor Green
} else {
    Write-Host "Existing .env found; preserving it." -ForegroundColor Yellow
}
& $VenvPython -m scripts.initialize_database
if ($LASTEXITCODE -ne 0) { throw "Database migration failed. Check PostgreSQL and .env; administrator was not created." }
& $VenvPython -m scripts.seed_departments
if ($LASTEXITCODE -ne 0) { throw "Department initialization failed." }
Write-Host "Create the first administrator; password input is hidden and requires 12 characters."
& $VenvPython -m scripts.create_admin
if ($LASTEXITCODE -ne 0) { throw "Administrator setup failed. Existing credentials were not replaced." }
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "M.Y. Business Hotel HR.lnk"
$Launcher = Join-Path $ProjectRoot "scripts\windows\start.ps1"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = (Get-Command powershell.exe).Source
$Shortcut.Arguments = ('-NoProfile -ExecutionPolicy Bypass -File "' + $Launcher + '"')
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.Description = "Start the local M.Y. Business Hotel HR application"
$Shortcut.Save()
$ReportTaskRegistrar = Join-Path $ProjectRoot "scripts\windows\register_monthly_report_task.ps1"
try {
    & $ReportTaskRegistrar
} catch {
    Write-Warning ("Could not register the monthly report task: " + $_.Exception.Message)
    Write-Warning "You can register it later by running scripts\windows\register_monthly_report_task.ps1."
}
Write-Host "Setup completed. Start the application from the desktop shortcut." -ForegroundColor Green
Write-Host "The application is local-only and does not auto-start with Windows."
