# Networking & security

## Network routing considerations

TwinBird isolates identities, configs, interfaces, and control channels, but the
host has one routing table and one DNS stack that both instances share. NetBird (not
TwinBird) manages routes and DNS. When running two accounts at once:

- Prefer non-overlapping peer address ranges per account where you control them.
  Overlap (including the default `100.64.0.0/10` range on both) can cause route and
  address conflicts.
- If both networks push a default route, or overlapping advertised subnets, they
  contend. Disable server routes on one instance if needed:
  the instance's NetBird config / `netbird up --disable-server-routes`.
- If DNS resolution misbehaves with both up, consider `--disable-dns` on one
  instance so only one instance manages the system resolver.
- TwinBird never enables IP forwarding and never creates routes between the two
  networks. For strict isolation, run each account in a separate OS user, VM, or
  network namespace.

See [../SECURITY.md](../SECURITY.md) for the full security model and boundaries.

## Security recommendations

- Do not run untrusted code under the same user account as your TwinBird instances;
  any same-user process can reach the NetBird control channel (this is NetBird's
  model).
- Keep secret setup keys out of shell history; prefer `TWINBIRD_SETUP_KEY` from a
  protected environment file, or OAuth login.
- On POSIX, TwinBird keeps instance files owner-only; do not loosen the config
  directory permissions.
