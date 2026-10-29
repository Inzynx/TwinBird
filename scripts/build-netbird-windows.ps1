param(
    [string]$OutputPath = (Join-Path $PSScriptRoot "..\dist\netbird-twinbird-0.64.2.exe"),
    [string]$SourcePath = (Join-Path $env:TEMP "twinbird-netbird-0.64.2-source"),
    [string]$WintunDllPath = (Join-Path $env:ProgramFiles "Netbird\wintun.dll")
)

$ErrorActionPreference = "Stop"

$goCommand = Get-Command go -ErrorAction SilentlyContinue
if (-not $goCommand) {
    throw "Go 1.25 or newer is required. Install Go, open a new PowerShell, and run this script again."
}

$goVersionText = (& go version 2>&1 | Out-String).Trim()
$goVersionMatch = [regex]::Match($goVersionText, 'go(?<version>\d+\.\d+)')
if (-not $goVersionMatch -or [version]$goVersionMatch.Groups["version"].Value -lt [version]"1.25") {
    throw "NetBird 0.64.2 requires Go 1.25 or newer. Found: $goVersionText"
}

$netbirdCommit = "06966da0121382297297d6838a90dd6e87d048ac"
if (Test-Path -LiteralPath $SourcePath) {
    $existingHead = (& git -C $SourcePath rev-parse HEAD 2>$null | Out-String).Trim()
    if ($existingHead -ne $netbirdCommit) {
        throw "SourcePath already exists and is not the pinned NetBird 0.64.2 source: $SourcePath"
    }
} else {
    & git clone --no-checkout https://github.com/netbirdio/netbird.git $SourcePath
    if ($LASTEXITCODE -ne 0) {
        throw "Could not clone the official NetBird source."
    }
    & git -C $SourcePath checkout --detach $netbirdCommit
    if ($LASTEXITCODE -ne 0) {
        throw "Could not check out the pinned NetBird 0.64.2 commit."
    }
}

$deviceFile = Join-Path $SourcePath "client\iface\device\device_windows.go"
$deviceSource = Get-Content -LiteralPath $deviceFile -Raw
if ($deviceSource -notmatch 'TWINBIRD_WINTUN_GUID') {
    $deviceSource = [regex]::Replace(
        $deviceSource,
        '(?m)(\t"net/netip"\r?\n)',
        '$1' + "`t`"os`"`r`n",
        1
    )
    if ($deviceSource -notmatch 'import \([\s\S]*?"os"') {
        throw "Could not add the environment import to NetBird's Windows adapter code."
    }

    $guidPattern = 'guidString := defaultWindowsGUIDSTring\s+if CustomWindowsGUIDString != "" \{\s+guidString = CustomWindowsGUIDString\s+\}'
    $guidReplacement = @'
guidString := defaultWindowsGUIDSTring
	if CustomWindowsGUIDString != "" {
		guidString = CustomWindowsGUIDString
	}
	if instanceGUID := os.Getenv("TWINBIRD_WINTUN_GUID"); instanceGUID != "" {
		guidString = instanceGUID
	}
'@
    $patchedSource = [regex]::Replace($deviceSource, $guidPattern, $guidReplacement.TrimEnd(), 1)
    if ($patchedSource -eq $deviceSource) {
        throw "Could not locate NetBird's fixed Windows GUID code; refusing to build an unpatched binary."
    }
    [System.IO.File]::WriteAllText(
        $deviceFile,
        $patchedSource,
        [System.Text.UTF8Encoding]::new($false)
    )
}

& gofmt -w $deviceFile
if ($LASTEXITCODE -ne 0) {
    throw "gofmt failed for the patched NetBird source."
}

$absoluteOutput = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $absoluteOutput
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
if (-not (Test-Path -LiteralPath $WintunDllPath -PathType Leaf)) {
    throw "wintun.dll was not found at '$WintunDllPath'. Supply -WintunDllPath to a compatible DLL."
}

$previousCgo = $env:CGO_ENABLED
try {
    $env:CGO_ENABLED = "0"
    Push-Location $SourcePath
    try {
        & go build `
            -trimpath `
            -ldflags "-X github.com/netbirdio/netbird/version.version=0.64.2" `
            -o $absoluteOutput `
            ./client
        if ($LASTEXITCODE -ne 0) {
            throw "Building the TwinBird-compatible NetBird client failed."
        }
    } finally {
        Pop-Location
    }
} finally {
    if ($null -eq $previousCgo) {
        Remove-Item Env:\CGO_ENABLED -ErrorAction SilentlyContinue
    } else {
        $env:CGO_ENABLED = $previousCgo
    }
}

$builtVersion = (& $absoluteOutput version 2>&1 | Out-String).Trim()
if ($builtVersion -notmatch '0\.64\.2') {
    throw "Built client reported an unexpected version: $builtVersion"
}

$wintunDestination = Join-Path $outputDirectory "wintun.dll"
if ([System.IO.Path]::GetFullPath($WintunDllPath) -ne $wintunDestination) {
    Copy-Item -LiteralPath $WintunDllPath -Destination $wintunDestination -Force
}

Write-Output "Built TwinBird-compatible NetBird client: $absoluteOutput"
Write-Output "Copied Wintun DLL to: $wintunDestination"
Write-Output 'An editable checkout automatically selects the client in its default dist directory unless TWINBIRD_NETBIRD_BIN is already set.'
Write-Output 'For a custom output path, set the following variable before launching TwinBird:'
Write-Output "`$env:TWINBIRD_NETBIRD_BIN = '$absoluteOutput'"
Write-Output 'Launch the TwinBird GUI as administrator with: twinbird gui'
