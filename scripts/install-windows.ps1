<#
.SYNOPSIS
    Install TwinBird on Windows: isolated venv, PATH entry, Start Menu shortcut,
    and (only when missing) the compatible NetBird client.

.DESCRIPTION
    Run from an elevated PowerShell. One command does the whole setup; use
    -Clean to remove a previous install first, and -NoLaunch to skip opening
    the GUI at the end.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1 -Clean
#>
param(
    [switch]$Clean,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'

# Resolve the repository root (the folder that contains this script's parent).
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$tbVenv = Join-Path $env:LOCALAPPDATA 'TwinBird\venv'
$tbScripts = Join-Path $tbVenv 'Scripts'
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\TwinBird.lnk'

foreach ($command in @('python')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command was not found on PATH. Install Python 3.10 or newer first."
    }
}

if ($Clean) {
    Write-Host 'Removing any previous TwinBird install...'
    Remove-Item $tbVenv -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $lnk -Force -ErrorAction SilentlyContinue
}

Write-Host 'Creating the isolated virtual environment...'
python -m venv $tbVenv
if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }

Write-Host 'Installing TwinBird and its GUI dependencies...'
& "$tbScripts\python.exe" -m pip install -e '.[gui]'
if ($LASTEXITCODE -ne 0) { throw 'Could not install TwinBird.' }

if (-not (Test-Path '.\dist\netbird-twinbird.exe') -or -not (Test-Path '.\dist\wintun.dll')) {
    Write-Host 'Building the compatible NetBird client (this needs Go and Git)...'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File '.\scripts\build-netbird-windows.ps1'
    if ($LASTEXITCODE -ne 0) { throw 'Could not build the compatible NetBird client.' }
}
$env:TWINBIRD_NETBIRD_BIN = (Resolve-Path '.\dist\netbird-twinbird.exe').Path

# Put the isolated venv on PATH: persist it for new shells and this session.
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$tbScripts*") {
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$tbScripts", 'User')
}
$env:Path = "$env:Path;$tbScripts"

# Add a Start Menu entry so Windows search finds "TwinBird" (pinning stays manual).
$icon = & "$tbScripts\python.exe" -c "import pathlib,twinbird;a=pathlib.Path(twinbird.__file__).parent/'assets';print(a/'logo-transparent.ico' if (a/'logo-transparent.ico').exists() else a/'logo.ico')"
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$shortcut.TargetPath = Join-Path $tbVenv 'Scripts\pythonw.exe'
$shortcut.Arguments = '-m twinbird.cli gui'
$shortcut.WorkingDirectory = $tbVenv
$shortcut.IconLocation = $icon
$shortcut.Description = 'TwinBird - manage multiple NetBird instances'
$shortcut.Save()

Write-Host ''
Write-Host "TwinBird is installed. 'twinbird' is on PATH (this shell and new ones),"
Write-Host 'and a Start Menu shortcut "TwinBird" was created. Connecting an instance'
Write-Host 'needs administrator rights (use the GUI Restart as administrator button).'

if (-not $NoLaunch) {
    & "$tbScripts\twinbird.exe" gui
}
