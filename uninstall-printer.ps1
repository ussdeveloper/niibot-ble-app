<#
.SYNOPSIS
    Removes the Niimbot IPP queue and the Niibot Studio start-up task.

.EXAMPLE
    .\uninstall-printer.ps1
#>
[CmdletBinding()]
param(
    [string]$PrinterName = "Niimbot B1",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$portName = "http://127.0.0.1:$Port/ipp/print"
$taskName = "Niibot Studio ($Port)"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Administrator rights are required - restarting elevated..." -ForegroundColor Yellow
    $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
              "-PrinterName", "`"$PrinterName`"", "-Port", $Port)
    Start-Process -FilePath "powershell.exe" -ArgumentList $args -Verb RunAs
    exit
}

if (Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue) {
    Remove-Printer -Name $PrinterName
    Write-Host "Removed the printer: $PrinterName" -ForegroundColor Green
}
if (Get-PrinterPort -Name $portName -ErrorAction SilentlyContinue) {
    Remove-PrinterPort -Name $portName
    Write-Host "Removed the port: $portName" -ForegroundColor Green
}
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed the start-up task: $taskName" -ForegroundColor Green
}
Write-Host "Done. Your data (the SQLite database) stays in the data\ folder." -ForegroundColor Cyan
