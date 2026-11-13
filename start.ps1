# Uruchamia panel Niibot Studio (tworzy .venv przy pierwszym starcie).
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host '[Niibot] Tworzę środowisko wirtualne .venv ...' -ForegroundColor Cyan
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .\.venv\Scripts\pip.exe install --quiet -r requirements.txt
}

if ($args.Count -eq 0) {
    & .\.venv\Scripts\python.exe -m niibot panel
} else {
    & .\.venv\Scripts\python.exe -m niibot @args
}
