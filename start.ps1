# Starts the Niibot Studio panel (creates .venv on the first run).
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host '[Niibot] Creating the .venv virtual environment ...' -ForegroundColor Cyan
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .\.venv\Scripts\pip.exe install --quiet -r requirements.txt
}

if ($args.Count -eq 0) {
    & .\.venv\Scripts\python.exe -m niibot panel
} else {
    & .\.venv\Scripts\python.exe -m niibot @args
}
