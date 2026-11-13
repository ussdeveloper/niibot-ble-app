#Requires -Version 5.1
<#
.SYNOPSIS
    Registers the printer as a normal Windows print queue (IPP) and starts the
    Niibot Studio service at logon.

.DESCRIPTION
    It does not install a kernel driver - it uses the inbox, Microsoft-signed
    "Microsoft IPP Class Driver". That way no EV certificate or test mode is
    needed and the printer shows up under "Devices and Printers" like any other.

    Requires administrator rights (the script elevates itself).

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

# ------------------------------------------------------------- elevation
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Administrator rights are required - restarting elevated..." -ForegroundColor Yellow
    $args = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-PrinterName", "`"$PrinterName`"",
        "-Port", $Port
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $args -Verb RunAs
    exit
}

Write-Step "Checking the environment"
if (-not (Test-Path $python)) {
    Write-Host "No .venv found - run start.bat first to install the dependencies." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $pythonw)) { $pythonw = $python }
Write-Ok "Python: $python"

$driver = Get-PrinterDriver -Name "Microsoft IPP Class Driver" -ErrorAction SilentlyContinue
if (-not $driver) {
    Write-Host "The 'Microsoft IPP Class Driver' is missing (Windows 10 1809+). Update the system." -ForegroundColor Red
    exit 1
}
Write-Ok "IPP driver: $($driver.Name)"

# ------------------------------------------------------- service autostart
Write-Step "Registering the Niibot Studio start-up task"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m niibot panel --port $Port --no-browser" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Ok "Task: $taskName"

Write-Step "Starting the service now"
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
    Write-Host "The service does not answer on port $Port yet - check the panel: http://127.0.0.1:$Port" -ForegroundColor Yellow
} else {
    Write-Ok "Panel is running: http://127.0.0.1:$Port"
}

# --------------------------------------------------------- IPP print queue
Write-Step "Creating the IPP port: $portName"
if (-not (Get-PrinterPort -Name $portName -ErrorAction SilentlyContinue)) {
    Add-PrinterPort -Name $portName
}
Write-Ok "Port ready"

Write-Step "Creating the printer '$PrinterName'"
$existing = Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Ok "The printer already exists - updating the port"
    Set-Printer -Name $PrinterName -PortName $portName
} else {
    Add-Printer -Name $PrinterName -DriverName "Microsoft IPP Class Driver" -PortName $portName
}
Start-Sleep -Seconds 2
Write-Ok "Printer registered"

$printer = Get-Printer -Name $PrinterName
Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Printer : $($printer.Name)  (port $($printer.PortName))"
Write-Host "  Panel   : http://127.0.0.1:$Port"
Write-Host ""
Write-Host "Pick '$PrinterName' in any application - the image is scaled to the"
Write-Host "format detected on the roll (RFID). Cropping and offset are set in"
Write-Host "the panel, on the 'Windows printer' tab."
