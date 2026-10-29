# Twinbird

> **Keep this file up to date.** After any significant change — new CLI commands, added modules,
> renamed files, changed platform logic, new env vars — update the relevant sections
> below. Stale instructions lead to wasted effort and broken assumptions. When in doubt, re-read
> the codebase and correct anything that has drifted.

## Overview

Cross-platform Python CLI tool for running multiple NetBird instances alongside the primary
installation. Each named instance gets an isolated config dir, daemon socket, and WireGuard
interface name.

## Project Structure

- `src/twinbird/cli.py` — Typer app, subcommands (`up`, `down`, `status`, `list`,
  `remove`, `autostart`, `gui`)
- `src/twinbird/manager.py` — GUI/CLI-agnostic orchestration (single source of
  truth for lifecycle). No Typer; progress via an `emit` callback, failures as
  `ManagerError`. Exposes `start/stop/set_autostart/remove/get_status/
  list_statuses/status_text` and the `InstanceStatus` dataclass.
- `src/twinbird/instance.py` — Thin Typer CLI layer delegating to `manager`
- `src/twinbird/gui.py` — tkinter desktop window + optional pystray tray; thin view
  over `manager`, actions on worker threads marshalled back via a queue
- `src/twinbird/detect.py` — Read-only detection of the primary NetBird config
  (management URL, WgIface, WgPort). Never writes. Tolerates ACL-restricted
  configs (common on Windows ProgramData).
- `src/twinbird/assets/` — bundled `logo.png` / `logo.ico` (shipped via
  `force-include` in pyproject).
- `src/twinbird/config.py` — Config dir resolution, instance metadata (incl.
  `wireguard_port`), PID files
- `src/twinbird/daemon.py` — Daemon process management
- `src/twinbird/netbird.py` — Shelling out to netbird binary, including version
  and JSON status queries
- `src/twinbird/platform.py` — OS-specific logic (paths, sockets, interface names)
- `tests/` — Unit tests (mocked, no real netbird needed). `test_gui.py` imports the
  GUI module and checks tray helpers without a display.

## Commands

```
uv sync                    # Install dependencies
uv run pytest -v           # Run tests
uv run ruff format .       # Format code
uv run ruff check --fix .  # Lint code
uv run twinbird --help     # Run CLI
```

## Architecture Notes

- All platform branching is in `platform.py` — other modules are OS-agnostic
- `netbird.py` is a thin wrapper around `subprocess.run` — no business logic
- `manager.py` is the orchestration core; `instance.py` (CLI) and `gui.py` (desktop)
  are thin views over it. Put lifecycle logic in `manager.py`, not in views.
- Daemon is managed via PID files with stale-PID detection
- GUI never calls Tk from a worker thread: actions run on threads, UI updates are
  posted to a queue drained by a periodic `root.after` poll
- GUI connection attempts use a cancellation event; cancelling terminates the
  foreground `netbird up` process and stops the temporary daemon. On Windows the
  GUI detaches from the launching console so closing PowerShell does not kill it.
- Multi-instance safety: `manager` assigns a unique interface name AND a unique
  WireGuard UDP port per instance, avoiding the primary NetBird's (from `detect`)
  and other instances'. NetBird's own features (DNS/SSH/monitor/MTU) are left
  untouched. Windows limitation: stock NetBird 0.64.2 requests one fixed Wintun
  GUID for every interface, so a second simultaneous adapter fails regardless of
  name or elevation; multi-instance Windows requires a client build with
  per-instance GUID support. Cloud management URL is `https://api.netbird.io:443`.
- Adoption: `manager.import_primary` (CLI `import`, GUI "Scan for existing") adds
  an `InstanceMetadata(managed_externally=True)` pointing at the stock daemon addr
  (`platform.default_primary_daemon_addr`). For such entries manager proxies
  up/down to the existing daemon and never starts its own daemon, touches
  autostart, or deletes the primary config; `remove` only drops the TwinBird view.
  Idempotent via `_find_adopted_name`.

## Security-Relevant Behavior (see SECURITY.md)

- Daemon control channel is `tcp://127.0.0.1:<port>` on Windows and a per-instance
  unix socket elsewhere. NetBird (verified 0.64.2) does NOT support `npipe://` for
  `--daemon-addr` (only `unix`/`tcp`); the stock daemon itself uses
  `tcp://127.0.0.1:41731`. The gRPC server has no auth, so the loopback port is
  reachable by any local process - same model as stock NetBird. Ports avoid 41731
  and other instances.
- Per-instance isolation on Windows relies on redirecting `%PROGRAMDATA%` to the
  instance dir (`platform.managed_child_env` / `managed_netbird_home`), because
  NetBird 0.64 keeps active_profile.json/state.json in a global ProgramData dir
  regardless of `--config`. Adopted (external) instances must NOT isolate
  (`run_up(isolate=False)`) so they use the real daemon/data dir.
- Windows multi-instance TUN support requires a TwinBird-compatible NetBird
  binary. The build patch in `scripts/build-netbird-windows.ps1` makes NetBird
  read `TWINBIRD_WINTUN_GUID`; `platform.managed_child_env` assigns a stable GUID
  per instance, and Windows autostart tasks preserve that variable and
  `PROGRAMDATA`.
- Creating a WireGuard adapter requires admin (Windows) / root. Separately,
  stock NetBird 0.64.2 Windows builds use a fixed Wintun GUID, which prevents a
  second simultaneous interface even when elevated.
- Instance names are validated in `platform.validate_instance_name`
  (`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, no Windows reserved names). `instance.py`
  calls it at the `up`/`down`/`status <name>` entry points. This guards path
  traversal and shell/XML/systemd injection.
- Setup keys go through `--setup-key-file` (owner-only temp file in the instance
  dir, deleted after `netbird up`), never on the process command line.
- POSIX files/dirs are created owner-only (0700/0600) via `config._secure_mkdir`
  and `config._secure_write_text`. Windows relies on the inherited %APPDATA% ACL.
- `daemon.stop_daemon(..., netbird_bin)` verifies the PID's executable is a
  NetBird process (`is_daemon_process`) before terminating, to avoid killing a
  recycled PID. Best-effort: permits when the image path is unreadable.
- `config.seed_netbird_config` refuses to overwrite an unparseable existing
  `config.json` (it may hold the WireGuard private key); `read_metadata` tolerates
  corrupt JSON and unknown keys.
- `platform.resolve_interface_name(name, config, taken)` probes for a free
  interface index so instances with colliding hashes do not share an interface.

## Environment Variables

| Variable | Purpose |
|---|---|
| `TWINBIRD_MANAGEMENT_URL` | Default management URL |
| `TWINBIRD_SETUP_KEY` | Default setup key |
| `TWINBIRD_NETBIRD_BIN` | Path to netbird binary |
| `TWINBIRD_CONFIG_DIR` | Override root config directory |
