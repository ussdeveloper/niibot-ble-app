#Requires -Version 5.1
<#
.SYNOPSIS
    Builds the standalone Windows distribution of Niibot Studio.

.DESCRIPTION
    Creates (or reuses) a virtual environment, installs the runtime requirements plus
    PyInstaller, and packages everything into dist\NiibotStudio\NiibotStudio.exe.
    The version is read from niibot\version.py.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File build-exe.ps1
    powershell -ExecutionPolicy Bypass -File build-exe.ps1 -SkipInstall
#>
param(
    [switch]$SkipInstall,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$versionLine = Select-String -Path 'niibot\version.py' -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $versionLine) { throw 'Cannot read the version from niibot\version.py' }
$version = $versionLine.Matches[0].Groups[1].Value
Write-Host "Niibot Studio $version - building standalone EXE" -ForegroundColor Cyan

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host 'Creating virtual environment...'
    python -m venv .venv
}

if (-not $SkipInstall) {
    Write-Host 'Installing requirements + PyInstaller...'
    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install -r requirements.txt --quiet
    & $python -m pip install pyinstaller --quiet
}

if ($Clean) {
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
}

Write-Host 'Running PyInstaller...'
# PyInstaller reports progress on stderr; keep that from aborting the script.
$log = Join-Path $PSScriptRoot 'build-pyinstaller.log'
$nativePref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $python -m PyInstaller --noconfirm --clean --log-level WARN NiibotStudio.spec *> $log
$pyExit = $LASTEXITCODE
$ErrorActionPreference = $nativePref
if ($pyExit -ne 0) {
    Get-Content $log -Tail 40 | ForEach-Object { Write-Host $_ }
    throw 'PyInstaller failed'
}

$exe = Join-Path $PSScriptRoot 'dist\NiibotStudio\NiibotStudio.exe'
if (-not (Test-Path $exe)) { throw "Build finished but $exe is missing" }

# keep the version and the changelog next to the executable
Copy-Item CHANGELOG.md, README.md, LICENSE, THIRD-PARTY-NOTICES.md 'dist\NiibotStudio\' -Force

$sizeMb = [math]::Round((Get-ChildItem 'dist\NiibotStudio' -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
Write-Host ''
Write-Host "Done: $exe" -ForegroundColor Green
Write-Host "Version: $version   Size: $sizeMb MB"
Write-Host 'Run it with:  dist\NiibotStudio\NiibotStudio.exe panel'
