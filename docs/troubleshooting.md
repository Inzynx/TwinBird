# Troubleshooting

- `netbird not found on PATH`: install NetBird or set `TWINBIRD_NETBIRD_BIN` to the
  full path of the binary.
- Daemon fails to start: TwinBird prints the tail of `daemon.log` from the instance
  directory. Inspect that file for the NetBird error.
- Instance shows `stopped` but you expected it up: run `twinbird status <name>` to
  query NetBird directly; check the autostart entry still exists.
- `creating interface: Access is denied` (Windows) or interface errors on Linux:
  the daemon connected but could not create the WireGuard adapter. Run TwinBird as
  administrator (Windows) or root (Linux).
- `Cannot create a file when that file already exists` while creating a second
  Windows interface on stock NetBird 0.64.2: this is a fixed Wintun GUID collision,
  not an elevation failure. The GUI/CLI now explain the limitation before sending
  a setup key when the installed client version and existing interface are known.
- `setup key is invalid`: setup keys can be single-use or expired. Generate a fresh
  key in the dashboard, or leave the key blank to log in via your browser.
- Linux user service does not survive reboot: enable lingering
  (`loginctl enable-linger "$USER"`; see [how-it-works.md](how-it-works.md)).
- "refusing to overwrite config.json" on `up`: an existing `config.json` in the
  instance directory is corrupt. It may contain the WireGuard private key, so
  TwinBird will not clobber it. Inspect and remove it manually, then retry.
