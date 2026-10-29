# Changelog

All notable changes in this fork are documented here. This fork continues the
now-archived [`OseSem/twinbird`](https://github.com/OseSem/twinbird).

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] - fork

This is an experimental test build based on OseSem's TwinBird. It is not a
stable release. Windows has been exercised with NetBird 0.64.2; Linux and
macOS need further end-to-end verification.

The Linux smoke script has been run successfully under Ubuntu in WSL with two
unauthenticated NetBird 0.64.2 daemons, isolated state directories and working
status queries. VPN adapter creation and account enrollment were not part of it.

README includes the WSL window-only GUI screenshot and setup instructions for
using an independently installed Linux NetBird client.

The README now includes copyable startup steps and test status for Windows 11,
Ubuntu/WSL and macOS. A setup-key GUI connection was confirmed in WSL; macOS is
source-reviewed and remains untested on a Mac.

### Fixed

- Reduced default/minimum GUI sizes to 960 pixels wide / 640 × 460 pixels,
  with smaller fonts, buttons, headings and rows. Table columns now shrink and
  the scrollbar does not determine the window's minimum width. Removed the
  unnecessary page-size synchronization; equal tab buttons remain unchanged.
  Details and Log can be hidden from the toolbar for a compact view.
- Table scrollbars use separate grid cells, avoiding misplaced arrow controls.
  Enter activates rows, focused buttons and add-dialog submission; double-click
  on a heading no longer toggles a connection. Details and log stay visible when
  resizing, and Windows display scaling renders text and controls sharply.
- Instances and Help tabs now keep the same window/page dimensions when switching;
  button padding is more compact. Verified with live Tk windows on Windows and
  Ubuntu/WSL.
- Both tab buttons have equal width and height in every selection state. Removed
  Clam's inherited selected-tab padding, which previously changed their size.
- WSL startup examples use the same persistent configuration-directory default
  and retain an existing environment override so saved instances stay discoverable.
- Windows starts the GUI as a detached process and returns the PowerShell prompt
  immediately; the GUI process does not depend on the shell remaining open.
- An imported primary can be reconnected after Stop even though its daemon
  remains reachable. Buttons, double-click and tray actions use the primary's
  Management connection status. Unknown status permits a reconnect attempt;
  primary removal and autostart protections remain in place. Regression tests
  passed, with module imports and reconnect state also checked in Ubuntu/WSL.

### Added

- Refreshed desktop theme, local display names with Rename/F2, and a connection
  detail panel. Renaming preserves directories, identity, service paths and
  adapter IDs, including for adopted primary entries. Copy IP/peer/details from
  the context menu or copy details with Ctrl+C. Actual Tk window checks passed
  on Windows and Ubuntu/WSL; macOS remains untested.
- Windows build uses NetBird's existing GUID customization hook through an
  added initialization file, with no replacement of existing function bodies.
  Supports an explicit release version, checks two GUIDs before publishing the
  executable, and uses a version-independent output name with legacy fallback.
  Additional releases remain experimental until VPN operation is tested.
  Actual builds and two-ID checks passed for 0.64.2 and 0.64.1. The user reported
  a working connection with the revised 0.64.2 build; 0.64.1 remains build/check
  only. An identical Wintun DLL is
  reused, allowing a build while an existing daemon has that DLL open.
- GUI status snapshots with last-check time and a separate Management connection
  indicator; checks run at startup, after actions and on manual refresh.
- A separate Windows NetBird 0.64.2 build with an instance-specific Wintun GUID,
  plus a build script that copies the required DLL beside that executable.
- Cancellation for pending GUI connection requests and an administrator notice.

- **Integrate an existing NetBird** (`twinbird import`, or the GUI "Scan for
  existing" button): adopts the already-installed NetBird as an instance by
  pointing at its running daemon. Idempotent (no duplicate on repeat scans), never
  starts a second daemon, and `Remove` is disabled for the adopted primary.
  Its autostart stays owned by the NetBird
  service.
- **Detect an existing NetBird install (read-only)**: TwinBird finds the primary
  config (`default.json`/`config.json` under ProgramData or `/var/lib/netbird`)
  and pre-fills the correct management URL in the GUI. It never modifies that
  config.
- **Per-instance WireGuard port**: each instance gets its own UDP port and keeps
  off the primary NetBird's port (usually 51820) and other instances', so two
  interfaces never clash. Stored in instance metadata and stable across restarts.
- Bundled multi-resolution Windows icon (`logo.ico`) and a taskbar AppUserModelID
  for a crisp taskbar/title-bar icon; higher-quality in-app logo.
- **Desktop manager (`twinbird gui`)**: a tkinter window plus an optional
  system-tray icon (bottom-right on Windows).
  - Instances tab: table of all instances with state, adapter, autostart and
    management URL; Start/Stop/Autostart/Remove/Add/Refresh actions.
  - Selection-driven buttons: actions enable only when they make sense, the first
    instance is auto-selected, and double-clicking a row starts/stops it (fixes
    the confusing "select an instance first" message).
  - Empty-state screen guiding first-time setup.
  - Redesigned Add-instance dialog with inline guidance for every field (Name,
    Management URL, Setup key, Interface) and a pre-filled Cloud URL.
  - **Help tab** explaining what TwinBird does, setup steps, each field, the
    buttons, multi-account routing, security, and CLI equivalents.
  - Bundled bird logo used for the window icon, tray icon and in-app header.
- **New CLI commands**: `remove` (stop and delete an instance), `autostart`
  (`--enable` / `--disable`), and `gui`.
- Optional `gui` install extra (`pystray`, `Pillow`) for the tray icon.
- `manager.py`: a GUI/CLI-agnostic orchestration layer that is now the single
  source of truth for instance lifecycle.
- Interface-name collision avoidance when deriving adapters for multiple
  instances.
- `.github/dependabot.yml` for weekly pip and GitHub Actions updates.
- `SECURITY.md` (threat model, findings, boundaries) and this changelog.

### Changed

- macOS launchd entries now pass each instance's `NB_STATE_DIR`, resolve the
  NetBird executable path, and escape paths in the plist. Registration does not
  immediately start a second daemon when the instance is already reachable.

- The adopted primary cannot be removed. Managed-instance removal verifies that
  its daemon and startup entry are stopped before deleting local state, and reports
  cleanup failures instead of silently claiming success.
- Documented that supported enrollment targets server/agent peers via setup key;
  NetBird user-device sign-up through its tray icon and email is unsupported.

- Non-interactive Windows child processes start without console windows, and
  status reads are bounded and run outside the GUI thread.
- Linux and macOS managed daemons use a separate `NB_STATE_DIR` per instance;
  systemd user units retain it across starts.

- **Per-instance isolation via a redirected `%PROGRAMDATA%`** so each NetBird
  daemon uses its own data directory (identity, keys, state). Without this,
  NetBird 0.64 shares one global `C:\ProgramData\Netbird` directory and instances
  collide. Verified live against NetBird 0.64.2.
- **Windows daemon control channel is `tcp://127.0.0.1:<port>`** (a distinct port
  per instance, avoiding the stock 41731). The NetBird agent does not support
  `npipe://` for `--daemon-addr`; an earlier attempt to use a named pipe is
  reverted. Bound to loopback only.
- Each instance also gets a distinct WireGuard UDP port (avoiding the primary's
  51820 and other instances').
- `instance.py` is now a thin Typer layer over `manager.py`.
- Expanded README (logo, badges, fork notice, install options, GUI walkthrough,
  routing and security sections); updated `AGENTS.md`.
- `release.yml` uses least-privilege, per-job permissions.

### Security

- The daemon control channel is loopback-only TCP on Windows (same model as the
  stock NetBird daemon on 41731); documented as a residual boundary since NetBird's
  gRPC server has no auth and does not support an authenticated local transport on
  this version.
- Strict instance-name validation (filesystem, pipe/socket, systemd/Task
  Scheduler, shell/XML) prevents path traversal and command injection.
- Setup keys are passed via `--setup-key-file` (owner-only on POSIX, deleted after use),
  never on a process command line; never stored.
- Owner-only permissions (0700/0600) for config dir and files on POSIX.
- `stop` verifies the tracked PID is a NetBird process before terminating it,
  avoiding killing a recycled PID.

### Fixed

- Corrected the NetBird Cloud management URL used by the GUI default and docs to
  `https://api.netbird.io:443` (the API endpoint; `app.netbird.io` is the
  dashboard, not a valid management URL).
- GUI usability: selection-aware buttons, auto-select, double-click toggle,
  empty-state, clearer Add dialog, more spacing, and a proper window icon.
- Fixed overlapping text in the Add dialog (the "detected URL" note collided with
  a field's help line); the dialog now uses vertical stacking.
- Detection tolerates ACL-restricted primary configs (common on Windows) instead
  of raising.
- `seed_netbird_config` no longer overwrites an unparseable `config.json` (which
  could discard the WireGuard private key).
- `read_metadata` tolerates corrupt JSON and unknown fields instead of crashing.
- Instance interfaces (and ports and control address) stay stable across restarts.
- A clear hint is shown when `up` fails because the WireGuard adapter needs
  administrator rights.
- `python -m twinbird.cli` now works (added a `__main__` entry point).
- Daemon log file handle is no longer leaked by the launcher.

### Tests / quality

- Added tests for the manager layer, multiple simultaneous instances, autostart,
  removal, name validation, PID-reuse safety, permissions and the GUI module.
- Full gate passing: ruff, ruff format, pyright (0 errors), pytest.
