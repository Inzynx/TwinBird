# Install & platforms

TwinBird is **not on PyPI**. Install it from this checkout; installing `twinbird`
from PyPI or cloning upstream gives you the original project, not this version.
Each platform needs a NetBird CLI installed and a setup key for a server/agent peer.

> **Experimental test project.** This working tree builds on
> [`OseSem/twinbird`](https://github.com/OseSem/twinbird), originally created by
> OseSem. Windows multi-instance connection has been exercised with NetBird
> 0.64.2. Linux and macOS support remain experimental. This is not a stable
> release. See [../CHANGELOG.md](../CHANGELOG.md) for changes.

> **Windows version support:** The build defaults to NetBird **0.64.2**, the
> version previously tested with multiple connections on Windows 11. It creates
> a separate client using NetBird's existing adapter-ID customization hook and
> checks that two different IDs are honored. Other release versions can be
> requested with `-Version`, but remain experimental until VPN connections have
> been tested. This does not guarantee compatibility with every NetBird release.
> Your existing NetBird installation is not replaced or modified and does not
> necessarily need to be version 0.64.2.
>
> The revised build script has built 0.64.2 and 0.64.1 successfully and checked
> two adapter IDs for each. The user subsequently reported a working connection
> with the revised 0.64.2 client. Version 0.64.1 has only passed the build and
> adapter-ID checks; routing and DNS were not independently reverified.

## What has been checked

| Platform | What has been checked |
|---|---|
| Windows 11 | Multi-instance connection tested with NetBird 0.64.2 and the separate TwinBird-compatible Windows client. |
| Ubuntu on WSL | Separate daemons and status queries tested; a setup-key connection was also confirmed in the GUI. Reboot persistence is unverified. |
| Native Linux | Experimental; the WSL checks do not establish native distribution compatibility. |
| macOS | Source reviewed and startup configuration covered by mocked tests. No Mac or live VPN test has been performed. |

## Requirements

- [NetBird](https://netbird.io) installed and on `PATH` (or point TwinBird at it
  with `TWINBIRD_NETBIRD_BIN`).
- Python 3.10 or newer.
- Windows 11 is the primary tested target. Linux and macOS are experimental.

The `[gui]` extra installs the modern Sun Valley theme (`sv-ttk`) and the optional
tray dependencies (`pystray`, `Pillow`); tkinter is also needed. Without the extra
the window still works, using the default Tk theme and no tray.

## Windows 11

Install Python 3.10 or newer, Git, Go 1.25 or newer and NetBird first. Open
PowerShell **as administrator** in the checkout and run the installer:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1
```

Add `-Clean` to remove a previous install first, or `-NoLaunch` to skip opening
the GUI at the end:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1 -Clean
```

That single command does everything:

- Creates an isolated virtual environment at `%LOCALAPPDATA%\TwinBird\venv` and
  installs TwinBird with its GUI dependencies.
- Builds the separate compatible NetBird client **only** when its executable or
  Wintun DLL is missing (that step needs Go and Git; it uses the installed
  NetBird's `wintun.dll` and leaves your NetBird installation in place).
- Adds the venv's `Scripts` directory to your user `PATH`, so `twinbird` works in
  any new shell, for example `twinbird gui` or `twinbird status`. The command is
  `twinbird gui` (a subcommand), not `twinbird -gui`.
- Creates a **Start Menu** shortcut (`TwinBird`) with the app icon, so Windows
  search finds the app instead of only the folder. Pin it yourself if you want.

The shortcut and `twinbird gui` launch the manager as a detached, windowless
process (via `pythonw.exe`), so no console window lingers and closing the
PowerShell window does not stop the GUI. The shortcut is not elevated; connecting
an instance needs administrator rights. The GUI header shows whether it is
running as **Administrator** or as a **Standard user (not elevated)**, and when
not elevated it offers a **Restart as administrator** button (you can also just
launch from an elevated shell) for when you create adapters.

To uninstall, run the uninstaller from the checkout. It removes the venv, the
Start Menu shortcut and the PATH entry, and leaves your NetBird installation
untouched. Remove your instances from the GUI first so their autostart entries
and adapters are cleaned up. Add `-Purge` to also delete the config/state
directory (`%APPDATA%\twinbird`), which discards saved instances:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall-windows.ps1
```

**Why a separate client?** Each connection needs its own adapter name **and**
internal Windows adapter ID. Separate names alone do not avoid the fixed-ID
collision in stock NetBird 0.64.2. TwinBird gives each connection its own stable
ID, and this build teaches the separate client to use it. You do not need to
choose IDs yourself.

### The separate Windows client in detail

NetBird 0.64.2's Windows TUN implementation requests the same fixed Wintun GUID
for every interface. Windows rejects the second adapter even when TwinBird gives
it a different name such as `wt34` and the process is elevated. Authentication
can register a peer before adapter creation fails, leaving a recently seen peer
in the NetBird dashboard without a working local interface. Administrator rights
do not resolve this GUID collision. Windows multi-instance support requires a
NetBird client build that reads a per-instance Wintun GUID from
`TWINBIRD_WINTUN_GUID`. TwinBird supplies a build script at
`scripts/build-netbird-windows.ps1`, defaulting to NetBird 0.64.2. Install Go 1.25
or newer and Git, then run the script once from the checkout in PowerShell:

```powershell
.\scripts\build-netbird-windows.ps1
twinbird gui
```

The script adds a small Windows initialization file that passes the instance ID
to NetBird's existing `CustomWindowsGUIDString` hook. It does not search and
replace NetBird function bodies. It checks the release tag and source commit,
builds to a temporary output, and verifies two IDs through NetBird's actual GUID
function before publishing `dist/netbird-twinbird.exe`. If compilation or the
GUID checks fail, the previous executable is kept. These checks create no
adapter and do not authenticate a peer.

To try another published release, pass `-Version` with its numeric release
version. The script checks that the required hook still compiles and works;
this is not proof of VPN, DNS, routing, or CLI compatibility. Only 0.64.2 has
previously been tested with Windows multi-instance connections. Existing
modified source directories are rejected rather than overwritten; use a fresh
`-SourcePath` if needed. The old `netbird-twinbird-0.64.2.exe` remains a fallback
for editable installs when the new executable is absent.

`pip install -e ".[gui]"` does not run this script. The resulting `dist` files
are local build outputs and are not included in a Git checkout or Python wheel.
If the compatible executable and `wintun.dll` are already present in `dist`,
you can launch TwinBird without building them again. Start the GUI as an
administrator to allow adapter creation.

An editable checkout discovers the default binary in `dist` automatically.
An explicitly set `TWINBIRD_NETBIRD_BIN` takes precedence; set it to the
compatible executable if you use a different location:

```powershell
$env:TWINBIRD_NETBIRD_BIN = "C:\path\to\netbird-twinbird.exe"
twinbird gui
```

The build script copies `wintun.dll` from the installed NetBird directory next
to the custom executable; use `-WintunDllPath` if the DLL is elsewhere. The
installed NetBird executable and its files are not changed. The build step is
specific to Windows; it is not required for Linux or WSL.

TwinBird assigns a stable unique GUID to each instance. Keep the custom binary
selected for the TwinBird GUI and its managed instances; the stock binary ignores
the per-instance GUID variable.

## Install from source (any platform, Python app only)

Use this working tree or a clone containing these changes. This installs the
Python application and dependencies only. It does **not** build or install the
separate Windows NetBird client described above.

```bash
cd twinbird
pip install -e ".[gui]"
twinbird gui
```

## Ubuntu and Ubuntu on WSL

Install the [NetBird Linux CLI](https://docs.netbird.io/get-started/install/linux)
inside Linux or WSL and ensure `netbird` is on `PATH`. On Ubuntu, paste this in
the checkout. On WSL, a working WSLg graphical session is required:

```bash
if ! command -v netbird >/dev/null; then
  echo 'Install the NetBird Linux CLI inside this environment first.'
elif sudo apt-get update && sudo apt-get install -y python3-venv python3-tk &&
     python3 -m venv "$HOME/.venvs/twinbird" &&
     "$HOME/.venvs/twinbird/bin/python" -m pip install -e '.[gui]'; then
  export TWINBIRD_NETBIRD_BIN="$(command -v netbird)"
  export TWINBIRD_CONFIG_DIR="${TWINBIRD_CONFIG_DIR:-$HOME/.config/twinbird}"
  sudo --preserve-env=DISPLAY,WAYLAND_DISPLAY,XDG_RUNTIME_DIR,TWINBIRD_NETBIRD_BIN,TWINBIRD_CONFIG_DIR \
    "$HOME/.venvs/twinbird/bin/twinbird" gui
else
  echo 'TwinBird installation failed; check the error above.'
fi
```

The GUI runs elevated so it can create an interface. Its configured files are
therefore root-owned. WSL displays a window without a tray icon; **Scan for
existing** searches inside WSL and does not import the Windows connection.
On Linux/WSL the GUI runs in the foreground: keep this terminal open while
using the window. Automatic background launch currently applies to Windows.
The config-directory setting preserves an existing `TWINBIRD_CONFIG_DIR` value.
Reuse the directory from previous sessions to see those saved instances; using
a different directory opens a separate local setup.

### Run the GUI in WSL

WSLg can display TwinBird's window, but WSL does not provide the system tray
manager used by the optional tray icon. TwinBird detects WSL and runs in
window-only mode. Install the NetBird Linux CLI inside WSL; this is a separate
Linux installation and peer from NetBird running on Windows. Follow the
[official NetBird Linux installation guide](https://docs.netbird.io/get-started/install/linux).

From the TwinBird checkout inside WSL, install Tk and TwinBird's GUI extra:

```bash
sudo apt update
sudo apt install -y python3-venv python3-tk
python3 -m venv ~/.venvs/twinbird
~/.venvs/twinbird/bin/pip install -e ".[gui]"
```

Launch the GUI with permission to create a WireGuard interface. The environment
variables keep TwinBird's WSL configuration separate and point it at the NetBird
binary installed inside WSL:

```bash
export TWINBIRD_NETBIRD_BIN="$(command -v netbird)"
export TWINBIRD_CONFIG_DIR="${TWINBIRD_CONFIG_DIR:-$HOME/.config/twinbird}"
sudo --preserve-env=DISPLAY,WAYLAND_DISPLAY,XDG_RUNTIME_DIR,TWINBIRD_NETBIRD_BIN,TWINBIRD_CONFIG_DIR \
  "$HOME/.venvs/twinbird/bin/twinbird" gui
```

Use the same `TWINBIRD_CONFIG_DIR` as your previous sessions if you already have
instances. For a disposable test you can set it to a separate `/tmp` directory
before running these commands; `/tmp` state may disappear after a reboot.

In the GUI, choose **Add instance** and enter a setup key for a server/agent peer.
Skip **Scan for existing** unless you already have a NetBird installation inside
WSL; that scan cannot see your Windows NetBird client. Removing the TwinBird
instance deletes its local state, while its remote peer must be removed separately
in the NetBird dashboard.

## macOS: experimental, not yet tested

Install Python with tkinter and the [NetBird macOS CLI](https://docs.netbird.io/get-started/install/macos),
and ensure both `python3` and `netbird` are on `PATH`. The following is a starting
point for a Mac test; GUI elevation, interface creation and autostart have not
been validated on macOS:

```bash
if ! command -v netbird >/dev/null; then
  echo 'Install the NetBird macOS CLI first.'
elif ! python3 -c 'import tkinter' >/dev/null 2>&1; then
  echo 'Install Python 3 with tkinter first.'
elif python3 -m venv "$HOME/.venvs/twinbird" &&
     "$HOME/.venvs/twinbird/bin/python" -m pip install -e '.[gui]'; then
  export TWINBIRD_NETBIRD_BIN="$(command -v netbird)"
  export TWINBIRD_CONFIG_DIR="$HOME/Library/Application Support/TwinBird"
  sudo --preserve-env=TWINBIRD_NETBIRD_BIN,TWINBIRD_CONFIG_DIR \
    "$HOME/.venvs/twinbird/bin/twinbird" gui
else
  echo 'TwinBird installation failed; check the error above.'
fi
```

## Upgrading

- Update this checkout and reinstall it with `pip install -e ".[gui]"`.
- Upgrading NetBird can change compatibility. On Windows the separate patched
  client is pinned to 0.64.2; rebuild and verify compatibility before upgrading it.
- Instance metadata is forward-compatible: older `instance.json` files load, and
  unknown fields from newer versions are ignored.
