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
if ($userInfo.Count -ne 2 -or [string]::IsNullOrWhiteSpace($userInfo[1]) -or [string]::IsNullOrWhiteSpace($uri.Host) -or $uri.AbsolutePath.Trim('/') -eq '') { throw "DATABASE_URL must include username, password, host, and database name." }
if ($uri.Host -notin @("127.0.0.1", "localhost", "::1")) { throw "Backups are restricted to the local PostgreSQL server (127.0.0.1, localhost, or ::1)." }
$dbUser = [Uri]::UnescapeDataString($userInfo[0])
$dbPassword = [Uri]::UnescapeDataString($userInfo[1])
$dbName = [Uri]::UnescapeDataString($uri.AbsolutePath.Trim('/'))
$dbPort = if ($uri.IsDefaultPort) { "5432" } else { [string]$uri.Port }
$pgDump = Get-Command pg_dump -ErrorAction SilentlyContinue
$pgRestore = Get-Command pg_restore -ErrorAction SilentlyContinue
if (-not $pgDump -or -not $pgRestore) { throw "pg_dump and pg_restore must be installed and available in PATH." }
if ([string]::IsNullOrWhiteSpace($BackupRoot)) { $BackupRoot = Join-Path $ProjectRoot "backups" }
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$destination = Join-Path $BackupRoot "MY-HR-$stamp"
$workingDestination = Join-Path $BackupRoot "MY-HR-$stamp.incomplete"
if ((Test-Path -LiteralPath $destination) -or (Test-Path -LiteralPath $workingDestination)) { throw "A backup with this timestamp already exists; retry in a moment." }
New-Item -ItemType Directory -Path $workingDestination | Out-Null
$env:PGPASSWORD = $dbPassword
try {
    $dumpPath = Join-Path $workingDestination "database.dump"
    Invoke-Checked $pgDump.Source @("--host=$($uri.Host)", "--port=$dbPort", "--username=$dbUser", "--dbname=$dbName", "--no-password", "--format=custom", "--no-owner", "--no-acl", "--file=$dumpPath")
    if (-not (Test-Path -LiteralPath $dumpPath -PathType Leaf) -or (Get-Item -LiteralPath $dumpPath).Length -le 0) { throw "pg_dump did not produce a non-empty database dump." }
    $archiveListing = & $pgRestore.Source "--list" $dumpPath
    if ($LASTEXITCODE -ne 0 -or -not $archiveListing) { throw "pg_restore --list could not validate the database dump." }
} catch {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    throw
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

try {
    $photoSource = Join-Path $ProjectRoot "var\employee_photos"
    $photoDestination = Join-Path $workingDestination "employee_photos"
    New-Item -ItemType Directory -Path $photoDestination | Out-Null
    if (Test-Path -LiteralPath $photoSource -PathType Container) {
        $photoItems = @(Get-ChildItem -LiteralPath $photoSource -Force)
        foreach ($item in $photoItems) {
            Copy-Item -LiteralPath $item.FullName -Destination $photoDestination -Recurse -Force
        }
    }
    $photoManifest = @()
    $sourceFiles = @()
    if (Test-Path -LiteralPath $photoSource -PathType Container) { $sourceFiles = @(Get-ChildItem -LiteralPath $photoSource -File -Recurse -Force) }
    foreach ($sourceFile in $sourceFiles) {
        $relativePath = $sourceFile.FullName.Substring($photoSource.Length).TrimStart('\','/') -replace '\\','/'
        $copiedPath = Join-Path $photoDestination $relativePath
        if (-not (Test-Path -LiteralPath $copiedPath -PathType Leaf)) { throw "Employee photo was not copied: $relativePath" }
        $sourceHash = (Get-FileHash -LiteralPath $sourceFile.FullName -Algorithm SHA256).Hash
        $copiedHash = (Get-FileHash -LiteralPath $copiedPath -Algorithm SHA256).Hash
        if ($sourceHash -ne $copiedHash) { throw "Employee photo checksum mismatch: $relativePath" }
        $photoManifest += [ordered]@{ path = $relativePath; length = $sourceFile.Length; sha256 = $sourceHash }
    }
    $hash = (Get-FileHash -LiteralPath (Join-Path $workingDestination "database.dump") -Algorithm SHA256).Hash
    $manifest = [ordered]@{
        format_version = 2
        created_at_local = (Get-Date).ToString("o")
        database_name = $dbName
        database_dump = "database.dump"
        database_dump_sha256 = $hash
        employee_photos_directory = "employee_photos"
        employee_photo_count = $photoManifest.Count
        employee_photos = $photoManifest
        includes_env_file = $false
        backup_complete = $true
        note = "Keep the matching .env/session secret separately and securely. Restore only into an empty database."
    }
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $workingDestination "manifest.json") -Encoding UTF8
    Move-Item -LiteralPath $workingDestination -Destination $destination
    Write-Host "Backup completed: $destination"
    Write-Host "Database dump SHA-256: $hash"
    Write-Host "Employee photos verified: $($photoManifest.Count)"
    Write-Host "The .env file and passwords were NOT copied into the backup."
} catch {
    Write-Error "Backup did not complete. The temporary folder is marked .incomplete and must not be used: $workingDestination"
    throw
}
