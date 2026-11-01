# Using an existing NetBird install

TwinBird does not change the existing NetBird installation or its configuration.
If you choose Start or Stop on an adopted instance, that command does affect its
existing connection; the GUI asks for confirmation first.

- **Integrate it (no re-adding).** Click **Scan for existing** in the GUI (or run
  `twinbird import`) to adopt your current NetBird as an instance. TwinBird points
  at its already-running daemon instead of starting a second one, so it is not a
  duplicate and you do not re-authenticate. Scanning twice does not add it again.
  An adopted instance shows autostart as `service` (owned by NetBird, not
  TwinBird). Remove is disabled for this entry, including through the CLI.
- **Detection (read-only).** TwinBird looks for the primary NetBird config
  (`C:\ProgramData\Netbird\default.json` on Windows, `/var/lib/netbird/default.json`
  on Linux/macOS) and, if it can read it, pre-fills the correct management URL in
  the Add dialog and uses it when adopting. It never writes to that file. On
  Windows the primary config is usually restricted to SYSTEM/Administrators, so
  this needs an elevated (administrator) TwinBird; otherwise the correct Cloud URL
  is used by default.
- **Remote peers.** Removing a managed instance deletes its local state and
  startup entry; it does not delete the peer from the NetBird dashboard. Remove
  that peer in the dashboard separately if needed.
- **No collisions.** TwinBird keeps every new instance off your primary's network
  adapter (usually `wt0`) and WireGuard UDP port (usually `51820`). Each instance
  gets its own adapter name and a unique port, so your existing connection stays
  stable.
- **NetBird stays in charge.** TwinBird does not disable DNS, SSH, the network
  monitor, MTU or routing. Those remain NetBird's own defaults/settings; TwinBird
  only sets the per-instance daemon address, adapter and port.
- **Separate identity.** Each instance is still its own NetBird peer, so it needs
  its own login or setup key. That is a NetBird requirement: the same identity
  cannot run on two interfaces at once.

See [install.md](install.md) for the Windows multi-instance limitation and the
separate client it requires.
