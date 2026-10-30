<#
.SYNOPSIS
    Uninstall TwinBird on Windows: remove the venv, the Start Menu shortcut and
    the PATH entry. Optionally also delete local state with -Purge.

.DESCRIPTION
    This removes the TwinBird application files only. It does NOT remove your
    separately installed NetBird client. Stop and remove your instances from the
    GUI (or `twinbird remove <name>`) BEFORE uninstalling, so managed daemons,
    autostart entries and network adapters are cleaned up first. -Purge then
    deletes the config/state directory as well (this discards saved instances).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall-windows.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall-windows.ps1 -Purge
#>
param(
    [switch]$Purge
)

$ErrorActionPreference = 'Stop'

$tbVenv = Join-Path $env:LOCALAPPDATA 'TwinBird\venv'
$tbRoot = Join-Path $env:LOCALAPPDATA 'TwinBird'
$tbScripts = Join-Path $tbVenv 'Scripts'
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\TwinBird.lnk'
if ($env:TWINBIRD_CONFIG_DIR) {
    $cfg = $env:TWINBIRD_CONFIG_DIR
} else {
    $cfg = Join-Path $env:APPDATA 'twinbird'
}

# Stop any running TwinBird GUI process so its files are not locked.
Write-Host 'Stopping any running TwinBird GUI...'
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*twinbird.cli*gui*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Remove the Start Menu shortcut.
if (Test-Path $lnk) {
    Remove-Item $lnk -Force -ErrorAction SilentlyContinue
    Write-Host "Removed Start Menu shortcut: $lnk"
}

# Remove the venv Scripts directory from the user PATH.
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath) {
    $parts = $userPath -split ';' | Where-Object { $_ -and ($_ -ne $tbScripts) }
    $newPath = ($parts -join ';')
    if ($newPath -ne $userPath) {
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        Write-Host 'Removed the TwinBird entry from your user PATH.'
    }
}

# Remove the virtual environment (and the TwinBird folder if now empty).
if (Test-Path $tbVenv) {
    Remove-Item $tbVenv -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Removed virtual environment: $tbVenv"
}
if ((Test-Path $tbRoot) -and -not (Get-ChildItem -Force $tbRoot -ErrorAction SilentlyContinue)) {
    Remove-Item $tbRoot -Force -ErrorAction SilentlyContinue
}

# Optionally remove the config/state directory (discards saved instances).
if ($Purge) {
    if (Test-Path $cfg) {
        Remove-Item $cfg -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Removed config/state directory: $cfg"
    }
} elseif (Test-Path $cfg) {
    Write-Host ''
    Write-Host "Kept your config/state at: $cfg"
    Write-Host 'Re-run with -Purge to delete it as well.'
}

Write-Host ''
Write-Host 'TwinBird uninstalled. Your NetBird installation was left untouched.'
Write-Host 'Any instances not removed beforehand may still have autostart entries'
Write-Host 'or network adapters; remove those from NetBird/Windows if needed.'
