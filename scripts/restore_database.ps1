[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$BackupFolder, [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot))
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
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$BackupFolder = [System.IO.Path]::GetFullPath($BackupFolder)
$manifestPath = Join-Path $BackupFolder "manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "manifest.json is missing." }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.format_version -ne 1) { throw "Unsupported backup format version." }
$dumpPath = Join-Path $BackupFolder ([string]$manifest.database_dump)
if (-not (Test-Path -LiteralPath $dumpPath -PathType Leaf)) { throw "Database dump is missing." }
$actualHash = (Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256).Hash
if ($actualHash -ne [string]$manifest.database_dump_sha256) { throw "Database dump checksum mismatch; restore aborted." }
$settings = Read-DotEnv (Join-Path $ProjectRoot ".env")
$dbUrl = [string]$settings["DATABASE_URL"]
if ([string]::IsNullOrWhiteSpace($dbUrl) -or $dbUrl -notmatch '^postgresql\+psycopg://') { throw "DATABASE_URL is not configured." }
$uri = [Uri]($dbUrl -replace '^postgresql\+psycopg://', 'postgresql://')
$userInfo = $uri.UserInfo.Split(':', 2)
if ($userInfo.Count -ne 2 -or $uri.AbsolutePath.Trim('/') -eq '') { throw "DATABASE_URL is incomplete." }
$dbUser = [Uri]::UnescapeDataString($userInfo[0])
$dbPassword = [Uri]::UnescapeDataString($userInfo[1])
$dbName = [Uri]::UnescapeDataString($uri.AbsolutePath.Trim('/'))
$dbPort = if ($uri.IsDefaultPort) { "5432" } else { [string]$uri.Port }
if ($dbName -ne [string]$manifest.database_name) { throw "Backup database '$($manifest.database_name)' does not match configured database '$dbName'." }
$pgRestore = Get-Command pg_restore -ErrorAction SilentlyContinue
$psql = Get-Command psql -ErrorAction SilentlyContinue
if (-not $pgRestore -or -not $psql) { throw "pg_restore and psql must be installed and available in PATH." }
$confirmation = Read-Host "This will restore database '$dbName'. Type RESTORE-$dbName to continue"
if ($confirmation -cne "RESTORE-$dbName") { throw "Confirmation did not match; no changes made." }
$env:PGPASSWORD = $dbPassword
try {
    $tableCount = (& $psql.Source "--host=$($uri.Host)" "--port=$dbPort" "--username=$dbUser" "--dbname=$dbName" "--tuples-only" "--no-align" "--command=SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p','S','v','m','f') AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%';")
    if ($LASTEXITCODE -ne 0) { throw "Could not verify that the target database is empty." }
    if ([int]("$tableCount".Trim()) -ne 0) { throw "Target database is not empty. Restore stopped to avoid overwriting data. Use a newly created empty database with the matching name." }

    $photoSource = Join-Path $BackupFolder ([string]$manifest.employee_photos_directory)
    $photoTarget = Join-Path $ProjectRoot "var\employee_photos"
    $photoStage = Join-Path $ProjectRoot "var\employee_photos.restore-staging"
    $photoPrevious = Join-Path $ProjectRoot "var\employee_photos.before-restore"
    if (Test-Path -LiteralPath $photoStage) { Remove-Item -LiteralPath $photoStage -Recurse -Force }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $photoTarget) | Out-Null
    New-Item -ItemType Directory -Path $photoStage | Out-Null
    if (Test-Path -LiteralPath $photoSource -PathType Container) {
        Copy-Item -Path (Join-Path $photoSource "*") -Destination $photoStage -Recurse -Force
    }
    if (Test-Path -LiteralPath $photoPrevious) { throw "Previous restore safety folder already exists: $photoPrevious" }
    if (Test-Path -LiteralPath $photoTarget) { Move-Item -LiteralPath $photoTarget -Destination $photoPrevious }
    Move-Item -LiteralPath $photoStage -Destination $photoTarget
    try {
        & $pgRestore.Source "--host=$($uri.Host)" "--port=$dbPort" "--username=$dbUser" "--dbname=$dbName" "--no-owner" "--no-acl" "--exit-on-error" "--single-transaction" $dumpPath
        if ($LASTEXITCODE -ne 0) { throw "pg_restore failed with exit code $LASTEXITCODE" }
    } catch {
        if (Test-Path -LiteralPath $photoTarget) { Remove-Item -LiteralPath $photoTarget -Recurse -Force }
        if (Test-Path -LiteralPath $photoPrevious) { Move-Item -LiteralPath $photoPrevious -Destination $photoTarget }
        throw
    }
    Write-Host "Restore completed. Previous employee photos, if any, remain in: $photoPrevious"
    Write-Host "Verify the restored application before deleting the previous photo folder."
} finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
