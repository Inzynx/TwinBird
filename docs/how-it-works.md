# How it works

Each named instance gets:

- Its own config directory: `%APPDATA%\twinbird\<name>\` on Windows,
  `~/.config/twinbird/<name>/` on Linux and macOS (owner-only on POSIX).
- Its own NetBird state and profile directory via `NB_STATE_DIR`. On Windows,
  `%PROGRAMDATA%` is also redirected for the managed daemon.
- Its own daemon control channel: a loopback TCP port on Windows, or a Unix
  socket on Linux and macOS. Windows avoids the stock port 41731.
- Its own WireGuard interface name (`wt<N>`, `utun<N>` on macOS) and its own
  WireGuard UDP port, both with collision avoidance against the primary and other
  instances.

TwinBird launches a dedicated `netbird service run` daemon per instance, then runs
`netbird up` against that daemon, fully isolated from the primary NetBird install.

> **Administrator / root is required** to create a WireGuard network adapter on
> Windows (and root on Linux). Run TwinBird elevated, or the daemon connects but
> fails at "creating interface: Access is denied".
>
> On Windows, administrator rights do not bypass the stock NetBird 0.64.2 fixed
> Wintun GUID limitation (see [install.md](install.md)).

## Supported NetBird peer type

TwinBird is for **server/agent peers enrolled with a setup key**. It does not
implement NetBird's interactive **user-device** sign-up and login flow. The NetBird
agent onboarding steps (clicking **Connect** from the tray icon and signing up with
your email address) start NetBird's user-device flow; TwinBird does not create or
manage that kind of peer. Create a setup key in the NetBird dashboard and use it
when adding a TwinBird instance. The optional browser/OAuth path is experimental
and does not add user-device support.

For an imported **primary**, **Stop** disconnects its VPN while the existing
NetBird service keeps running. After refresh, **Start** reconnects through that
same daemon; a disconnected service is shown as `disconnected (existing)`.
TwinBird does not replace the primary service or change its autostart. If the
service itself has been stopped outside TwinBird, start it through the operating
system's service manager first. In WSL, an imported primary belongs to Linux;
it is separate from the Windows NetBird installation.

## Authentication methods

- Setup key: use the GUI or `TWINBIRD_SETUP_KEY` to avoid putting the secret on
  TwinBird's command line. TwinBird passes it to NetBird through a temporary
  `--setup-key-file` and deletes the file after use. The CLI also accepts
  `--setup-key`, which exposes the value in TwinBird's own process arguments.
- OAuth / SSO: omit the setup key for the interactive CLI flow. GUI browser login
  is experimental.

## Environment variables

```bash
export TWINBIRD_MANAGEMENT_URL=https://mgmt.example.com
export TWINBIRD_SETUP_KEY=YOUR_KEY
twinbird up office
```

| Variable | Purpose |
|---|---|
| `TWINBIRD_MANAGEMENT_URL` | Default management URL |
| `TWINBIRD_SETUP_KEY` | Default setup key |
| `TWINBIRD_NETBIRD_BIN` | Path to the netbird binary (default: `netbird` on `PATH`) |
| `TWINBIRD_CONFIG_DIR` | Override the root config directory |

## Linux experimental internals

NetBird 0.64.2 reads `NB_STATE_DIR` at process startup. TwinBird sets it to each
instance directory for both daemon and client commands, and writes it into the
systemd user unit. This avoids sharing `/var/lib/netbird` with the primary daemon.
The installed `netbird` binary is used directly on Linux. A local Ubuntu WSL
smoke check started two unauthenticated NetBird 0.64.2 daemons, verified their
separate state directories and Unix sockets, and queried each daemon's status. It
did not create a WireGuard adapter or establish a VPN connection. To repeat the
check on Linux with a NetBird binary, run:

```bash
python3 scripts/linux-smoke.py "$(command -v netbird)"
```

WSL has its own Linux filesystem, processes and NetBird state. **Scan for existing**
inside WSL can only adopt a NetBird installation inside WSL; it cannot detect or
adopt the Windows NetBird client. Use **Add instance** with a setup key to enroll a
separate WSL peer.

Creating a WireGuard interface normally requires root or suitable Linux
capabilities. A systemd user service does not automatically receive those
privileges. User-bus availability, linger, capabilities, GUI access under
elevation, routing and DNS must be checked on the target distribution. Linux
multi-account connectivity and persistence have not yet been verified end to end.

## macOS feasibility and limits

NetBird 0.64.2's macOS client creates `utun` interfaces, and its profile code
honours `NB_STATE_DIR`. TwinBird assigns an interface name and isolated state
directory per instance, and includes that directory in the launchd configuration.
The implementation avoids loading a second launchd daemon while the same
instance's control socket is already active.

These are source-level and mocked checks, not a live Mac validation. In particular,
macOS may already use a selected `utun` number, and GUI elevation, route and DNS
interactions, launchd permissions and reconnect after login require testing. Treat
macOS support and autostart as experimental until those checks succeed.

## Autostart (experimental)

On a successful `up`, TwinBird attempts to register a per-instance startup entry.
`twinbird down` removes it. Registration and reconnect after reboot depend on
the platform and permissions; verify them on your machine.

- Windows: a hidden Task Scheduler task (`twinbird-<name>`) triggered at logon,
  running with highest available privileges. Registering it may prompt once for
  UAC elevation. If a task was created by an older checkout, disable and then
  enable autostart for that instance to regenerate it with its isolated paths.
- Linux: a systemd user unit (`twinbird-<name>.service`). User units only start at
  boot if lingering is enabled for your account, and still need permission to
  create the network interface:
  ```bash
  loginctl enable-linger "$USER"
  ```
- macOS: a launchd user agent (`com.twinbird.<name>.plist`) with `RunAtLoad` and
  `KeepAlive`.
