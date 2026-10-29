param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = '0.64.2',
    [string]$OutputPath = (Join-Path $PSScriptRoot '..\dist\netbird-twinbird.exe'),
    [string]$SourcePath = '',
    [string]$WintunDllPath = (Join-Path $env:ProgramFiles 'Netbird\wintun.dll')
)

$ErrorActionPreference = 'Stop'
foreach ($command in @('go', 'git')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command is required. Install Git and Go 1.25 or newer, then open a new PowerShell."
    }
}
if (-not (Test-Path -LiteralPath $WintunDllPath -PathType Leaf)) {
    throw "wintun.dll was not found at '$WintunDllPath'. Supply -WintunDllPath to a compatible DLL."
}
if (-not $SourcePath) {
    $SourcePath = Join-Path $env:TEMP "twinbird-netbird-$Version-clean-source"
}
$SourcePath = [System.IO.Path]::GetFullPath($SourcePath)
if (-not (Test-Path -LiteralPath $SourcePath)) {
    & git clone --depth 1 --branch "v$Version" https://github.com/netbirdio/netbird.git $SourcePath
    if ($LASTEXITCODE -ne 0) { throw "Could not clone official NetBird v$Version source." }
}
$sourceChanges = & git -C $SourcePath status --porcelain --untracked-files=all
if ($LASTEXITCODE -ne 0 -or $sourceChanges) {
    throw "SourcePath must be a clean NetBird checkout. Use a fresh -SourcePath; existing changes will not be overwritten."
}
$sourceCommit = (& git -C $SourcePath rev-parse HEAD | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not read source commit.' }
$tagCommit = (& git -C $SourcePath rev-parse "refs/tags/v$Version^{commit}" | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $tagCommit -ne $sourceCommit) {
    throw "SourcePath must be checked out at the exact v$Version tag."
}
if ($Version -eq '0.64.2' -and $sourceCommit -ne '06966da0121382297297d6838a90dd6e87d048ac') {
    throw 'The 0.64.2 source does not match the reviewed commit.'
}
if ($Version -ne '0.64.2') {
    Write-Warning "NetBird $Version is experimental here. Passing the GUID check does not validate VPN, DNS or routing compatibility."
}

# Use NetBird's customization variable, without rewriting its existing functions.
# The compiler checks the hook exists; --twinbird-check calls NetBird's getGUID()
# to prove it honors the ID. No adapter, daemon or login is created by this check.
$hookPath = Join-Path $SourcePath 'client\iface\device\twinbird_guid_windows.go'
$hookSource = @'
package device

import (
    "encoding/json"
    "fmt"
    "os"
    "strings"
)

func init() {
    if value := os.Getenv("TWINBIRD_WINTUN_GUID"); value != "" {
        CustomWindowsGUIDString = value
    }
    if len(os.Args) == 2 && os.Args[1] == "--twinbird-check" {
        requested := os.Getenv("TWINBIRD_WINTUN_GUID")
        actual, err := getGUID()
        if requested == "" || err != nil || !strings.EqualFold(actual.String(), requested) {
            fmt.Fprintln(os.Stderr, "TwinBird GUID check failed: NetBird did not honor the requested adapter ID")
            os.Exit(1)
        }
        _ = json.NewEncoder(os.Stdout).Encode(map[string]string{
            "capability": "per-instance-wintun-guid-v1",
            "guid": actual.String(),
        })
        os.Exit(0)
    }
}
'@
$absoluteOutput = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $absoluteOutput
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$stagingDirectory = Join-Path $outputDirectory ([guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stagingDirectory | Out-Null
$stagedBinary = Join-Path $stagingDirectory 'netbird-twinbird.exe'
$previousCgo = $env:CGO_ENABLED
$previousGuid = $env:TWINBIRD_WINTUN_GUID
try {
    [System.IO.File]::WriteAllText($hookPath, $hookSource, [System.Text.UTF8Encoding]::new($false))
    $env:CGO_ENABLED = '0'
    Push-Location $SourcePath
    try {
        & go build -trimpath -ldflags "-X github.com/netbirdio/netbird/version.version=$Version" -o $stagedBinary ./client
        if ($LASTEXITCODE -ne 0) {
            throw 'Build failed. The GUID hook may have changed or a newer Go toolchain may be required. The previous client has been kept.'
        }
    } finally { Pop-Location }

    $builtVersion = (& $stagedBinary version | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $builtVersion -ne $Version) {
        throw "Built client reported an unexpected version: $builtVersion"
    }
    foreach ($probeGuid in @('{7f63c202-2671-4d67-a5ba-c905b29881ae}', '{9596e334-a8ca-42c6-92c2-77e3242adfa9}')) {
        $env:TWINBIRD_WINTUN_GUID = $probeGuid
        $probeOutput = & $stagedBinary --twinbird-check
        if ($LASTEXITCODE -ne 0) { throw 'Built client failed the per-instance GUID check.' }
        $probe = $probeOutput | ConvertFrom-Json
        if ($probe.capability -ne 'per-instance-wintun-guid-v1' -or $probe.guid -ne $probeGuid) {
            throw 'Built client returned an unexpected GUID check result.'
        }
    }
    $dllDestination = Join-Path $outputDirectory 'wintun.dll'
    $dllAlreadyPresent = (Test-Path -LiteralPath $dllDestination) -and (
        (Get-FileHash -LiteralPath $WintunDllPath).Hash -eq (Get-FileHash -LiteralPath $dllDestination).Hash
    )
    # A running daemon can lock this DLL. Identical bytes need no replacement.
    if (-not $dllAlreadyPresent) {
        try { Copy-Item -LiteralPath $WintunDllPath -Destination $dllDestination -Force }
        catch { throw "Could not update wintun.dll. Stop TwinBird-managed instances before replacing a DLL in use. $($_.Exception.Message)" }
    }
    Move-Item -LiteralPath $stagedBinary -Destination $absoluteOutput -Force
} finally {
    if ($null -eq $previousCgo) { Remove-Item Env:\CGO_ENABLED -ErrorAction SilentlyContinue } else { $env:CGO_ENABLED = $previousCgo }
    if ($null -eq $previousGuid) { Remove-Item Env:\TWINBIRD_WINTUN_GUID -ErrorAction SilentlyContinue } else { $env:TWINBIRD_WINTUN_GUID = $previousGuid }
    # Only remove the individual files and empty directory created above.
    if (Test-Path -LiteralPath $hookPath) { Remove-Item -LiteralPath $hookPath }
    if (Test-Path -LiteralPath $stagedBinary) { Remove-Item -LiteralPath $stagedBinary }
    Remove-Item -LiteralPath $stagingDirectory
}
Write-Output "Built TwinBird-compatible NetBird $Version ($sourceCommit): $absoluteOutput"
Write-Output 'Two per-instance GUID checks passed. Test a VPN connection separately when using a new version.'
Write-Output 'The editable checkout selects dist\netbird-twinbird.exe unless TWINBIRD_NETBIRD_BIN is set.'
Write-Output "For a custom location: `$env:TWINBIRD_NETBIRD_BIN = '$absoluteOutput'"
Write-Output 'Launch the TwinBird GUI as administrator with: twinbird gui'
