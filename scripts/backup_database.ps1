[CmdletBinding()]
param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot), [string]$BackupRoot = "")
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
function Read-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing .env file: $Path" }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $value = $Matches[2].Trim()
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) { $value = $value.Substring(1, $value.Length - 2) }
            $values[$Matches[1]] = $value
        }
    }
    return $values
}
function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed with exit code $LASTEXITCODE" }
}
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$settings = Read-DotEnv (Join-Path $ProjectRoot ".env")
$dbUrl = [string]$settings["DATABASE_URL"]
if ([string]::IsNullOrWhiteSpace($dbUrl) -or $dbUrl -notmatch '^postgresql\+psycopg://') { throw "DATABASE_URL must be a configured postgresql+psycopg URL." }
$uri = [Uri]($dbUrl -replace '^postgresql\+psycopg://', 'postgresql://')
$userInfo = $uri.UserInfo.Split(':', 2)
if ($userInfo.Count -ne 2 -or [string]::IsNullOrWhiteSpace($uri.Host) -or $uri.AbsolutePath.Trim('/') -eq '') { throw "DATABASE_URL must include username, password, host, and database name." }
$dbUser = [Uri]::UnescapeDataString($userInfo[0])
$dbPassword = [Uri]::UnescapeDataString($userInfo[1])
$dbName = [Uri]::UnescapeDataString($uri.AbsolutePath.Trim('/'))
$dbPort = if ($uri.IsDefaultPort) { "5432" } else { [string]$uri.Port }
$pgDump = Get-Command pg_dump -ErrorAction SilentlyContinue
if (-not $pgDump) { throw "pg_dump was not found. Install PostgreSQL client tools and add their bin folder to PATH." }
if ([string]::IsNullOrWhiteSpace($BackupRoot)) { $BackupRoot = Join-Path $ProjectRoot "backups" }
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$destination = Join-Path $BackupRoot "MY-HR-$stamp"
New-Item -ItemType Directory -Path $destination | Out-Null
$env:PGPASSWORD = $dbPassword
try {
    $dumpPath = Join-Path $destination "database.dump"
    Invoke-Checked $pgDump.Source @("--host=$($uri.Host)", "--port=$dbPort", "--username=$dbUser", "--dbname=$dbName", "--format=custom", "--no-owner", "--no-acl", "--file=$dumpPath")
} finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
$photoSource = Join-Path $ProjectRoot "var\employee_photos"
$photoDestination = Join-Path $destination "employee_photos"
New-Item -ItemType Directory -Path $photoDestination | Out-Null
if (Test-Path -LiteralPath $photoSource -PathType Container) {
    $photoItems = @(Get-ChildItem -LiteralPath $photoSource -Force)
    if ($photoItems.Count -gt 0) { Copy-Item -Path (Join-Path $photoSource "*") -Destination $photoDestination -Recurse -Force }
}
$hash = (Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256).Hash
$manifest = [ordered]@{
    format_version = 1
    created_at_local = (Get-Date).ToString("o")
    database_name = $dbName
    database_dump = "database.dump"
    database_dump_sha256 = $hash
    employee_photos_directory = "employee_photos"
    employee_photo_count = @(Get-ChildItem -LiteralPath $photoDestination -File -Recurse).Count
    includes_env_file = $false
    note = "Keep the matching .env/session secret separately and securely. Restore only into an empty database."
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $destination "manifest.json") -Encoding UTF8
Write-Host "Backup completed: $destination"
Write-Host "Database dump SHA-256: $hash"
Write-Host "The .env file and passwords were NOT copied into the backup."
