<#
.SYNOPSIS
    Rejestruje Niimbota jako normalną drukarkę Windows (kolejka IPP) i uruchamia
    usługę Niibot Studio przy logowaniu.

.DESCRIPTION
    Nie instaluje sterownika jądra — używa wbudowanego, podpisanego przez Microsoft
    "Microsoft IPP Class Driver". Dzięki temu nie trzeba certyfikatu EV ani trybu
    testowego, a drukarka pojawia się w "Urządzenia i drukarki" jak każda inna.

    Wymaga uprawnień administratora (skrypt podnosi się sam).

.EXAMPLE
    .\install-printer.ps1
    .\install-printer.ps1 -PrinterName "Niimbot B1" -Port 8765
#>
[CmdletBinding()]
param(
    [string]$PrinterName = "Niimbot B1",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$portName = "http://127.0.0.1:$Port/ipp/print"
$taskName = "Niibot Studio ($Port)"
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$python = Join-Path $root ".venv\Scripts\python.exe"

function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "    $text" -ForegroundColor Green }

# ---------------------------------------------------------------- elevacja
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Potrzebne są uprawnienia administratora - uruchamiam ponownie..." -ForegroundColor Yellow
    $args = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-PrinterName", "`"$PrinterName`"",
        "-Port", $Port
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $args -Verb RunAs
    exit
}

Write-Step "Sprawdzam środowisko"
if (-not (Test-Path $python)) {
    Write-Host "Brak .venv - uruchom najpierw start.bat, żeby zainstalować zależności." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $pythonw)) { $pythonw = $python }
Write-Ok "Python: $python"

$driver = Get-PrinterDriver -Name "Microsoft IPP Class Driver" -ErrorAction SilentlyContinue
if (-not $driver) {
    Write-Host "Brak sterownika 'Microsoft IPP Class Driver' (Windows 10 1809+). Zaktualizuj system." -ForegroundColor Red
    exit 1
}
Write-Ok "Sterownik IPP: $($driver.Name)"

# ------------------------------------------------------- autostart usługi
Write-Step "Rejestruję uruchamianie Niibot Studio przy logowaniu"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m niibot panel --port $Port --no-browser" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Ok "Zadanie: $taskName"

Write-Step "Uruchamiam usługę teraz"
Get-ScheduledTask -TaskName $taskName | Start-ScheduledTask
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/state" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    Write-Host "Usługa jeszcze nie odpowiada na porcie $Port - sprawdź panel: http://127.0.0.1:$Port" -ForegroundColor Yellow
} else {
    Write-Ok "Panel działa: http://127.0.0.1:$Port"
}

# ------------------------------------------------------------- kolejka IPP
Write-Step "Tworzę port IPP: $portName"
if (-not (Get-PrinterPort -Name $portName -ErrorAction SilentlyContinue)) {
    Add-PrinterPort -Name $portName
}
Write-Ok "Port gotowy"

Write-Step "Tworzę drukarkę '$PrinterName'"
$existing = Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Ok "Drukarka już istnieje - aktualizuję port"
    Set-Printer -Name $PrinterName -PortName $portName
} else {
    Add-Printer -Name $PrinterName -DriverName "Microsoft IPP Class Driver" -PortName $portName
}
Start-Sleep -Seconds 2
Write-Ok "Drukarka zarejestrowana"

$printer = Get-Printer -Name $PrinterName
Write-Host ""
Write-Host "Gotowe." -ForegroundColor Green
Write-Host "  Drukarka : $($printer.Name)  (port $($printer.PortName))"
Write-Host "  Panel    : http://127.0.0.1:$Port"
Write-Host ""
Write-Host "Wybierz '$PrinterName' w dowolnej aplikacji - obraz zostanie przeskalowany"
Write-Host "do formatu wykrytego na rolce (RFID). Kadrowanie i przesunięcie ustawisz"
Write-Host "w panelu, w karcie 'Drukarka Windows'."
