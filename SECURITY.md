# TwinBird Security Model and Audit

This document describes TwinBird's security model, the audit findings that shaped
the current code, and the security boundaries TwinBird cannot enforce on its own.
TwinBird orchestrates the official NetBird agent; several properties below are
inherited from NetBird and are called out as such.

## Threat model

TwinBird runs on a multi-user or single-user workstation and launches one NetBird
daemon per instance under the invoking user. The relevant adversaries are:

- A local unprivileged process or user on the same machine (confidentiality and
  integrity of each instance's control channel, config, and credentials).
- Another TwinBird instance on the same host (isolation between a personal and a
  professional NetBird network).
- A supply-chain adversary (dependencies, GitHub Actions, release pipeline).

TwinBird does not defend against a local administrator/root, against a compromised
NetBird management server, or against kernel-level attackers. Those are outside
its control.

## NetBird daemon control channel (most important finding)

The NetBird daemon exposes a gRPC control socket:

- The gRPC server has no authentication and no TLS.
- A TCP connection carries no caller identity, so the daemon cannot tell who is
  calling. Every privileged operation is refused on TCP, but non-privileged
  operations (status, reading configuration, up/down) are available to anyone who
  can reach the port.
- A Unix socket caller is identified from the kernel (SO_PEERCRED / LOCAL_PEERCRED).

Important correction (verified against NetBird 0.64.2): the NetBird agent only
accepts `unix://` and `tcp://` for `--daemon-addr`. Named pipes (`npipe://`) are
NOT supported by the client - passing one fails with "unsupported daemon address
protocol: npipe". The stock NetBird daemon itself listens on
`tcp://127.0.0.1:41731`.

Current design: on Windows TwinBird uses `tcp://127.0.0.1:<port>` (a distinct port
per instance, avoiding 41731), bound to loopback only. This matches the stock
NetBird daemon's own model; it is not a regression. On Linux/macOS it uses a
per-instance Unix socket (SO_PEERCRED identity) inside the owner-only instance
directory for non-root use.

Residual boundary (cannot be removed on this NetBird version): the loopback TCP
control port has no authentication, so any process running on the machine can reach
it, just as it can reach the stock NetBird daemon on 41731. Compensating controls:
TwinBird binds to `127.0.0.1` only (never a routable interface), and privileged
daemon operations are still refused on TCP by NetBird. Do not run untrusted code on
a machine whose NetBird connections you need to protect. If a future NetBird build
supports authenticated local transports, prefer those.

## Credential handling

- Setup keys are secrets. NetBird accepts `--setup-key` (value on the command
  line) or `--setup-key-file` (path to a file). Process command lines are readable
  by other local users (`/proc/<pid>/cmdline` on Linux, `Win32_Process` on
  Windows). TwinBird writes the key to a temporary file inside the instance
  directory and passes `--setup-key-file`, then deletes the file. POSIX permissions
  are owner-only; on Windows the file inherits the directory ACL. The NetBird child
  command line does not include the key. Passing TwinBird's own `--setup-key`
  option exposes it in TwinBird's process arguments and potentially shell history;
  use the GUI or an environment variable to avoid that exposure.
- TwinBird does not store setup keys. The only credential at rest is the WireGuard
  private key, which the NetBird daemon writes into its own `config.json`. TwinBird
  does not read, copy, or transform it.
- The management URL is stored in `instance.json`. It is not a secret, but it is
  kept in the owner-only instance directory alongside the rest of the instance
  state.
- A custom encryption layer was deliberately not added. The OS file permissions
  below are the right control for these files, and NetBird owns the key lifecycle.

## File permissions

- POSIX: the config root, each instance directory, `instance.json`, `daemon.pid`,
  `daemon.log`, and the transient setup-key file are created with owner-only
  permissions (`0700` for directories, `0600` for files).
- Windows: `%APPDATA%\twinbird` inherits the user profile ACL (the user, SYSTEM,
  and Administrators). TwinBird relies on that inherited ACL rather than setting an
  explicit DACL. If your environment weakens default profile ACLs, tighten the
  `twinbird` directory manually.

## Input validation

Instance names flow into filesystem paths, named-pipe and socket names, systemd
unit names, scheduled-task names, and shell/XML interpolation. TwinBird validates
every name against `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` and rejects Windows reserved
device names. This closes:

- Path traversal via `..`, `/`, or `\` in the config root.
- Command injection through the Windows UAC-elevation helper (which interpolates
  the task name into a PowerShell command line).
- Injection into systemd unit files and Task Scheduler XML.

## Process lifecycle safety

- PID reuse: `stop` verifies that the tracked PID still refers to a NetBird
  process (by executable image) before terminating it. If the PID has been
  recycled to an unrelated process, TwinBird treats the record as stale and does
  not kill the foreign process. The check is best-effort: when the image path
  cannot be read, TwinBird proceeds (to avoid blocking a legitimate stop), so this
  reduces but does not fully eliminate the race.
- Config corruption: `seed_netbird_config` refuses to overwrite an existing
  `config.json` that is present but unparseable, because that file may hold the
  WireGuard private key. It raises instead of silently discarding credentials.
- Corrupt metadata: a damaged `instance.json` is treated as absent rather than
  crashing the CLI.

## Network isolation between instances

This is a correctness and security requirement for running a personal and a
professional network side by side. Key points and limitations:

- Interface names: each instance gets a distinct WireGuard interface. TwinBird now
  detects collisions between existing instances and probes for a free interface
  index, so two instances cannot silently share or fight over one interface.
- Routing and DNS are managed by NetBird, not TwinBird. TwinBird does not create
  routes between the two networks and never enables IP forwarding. However, host
  routing and DNS are global resources:
  - If both NetBird networks assign peer addresses from the same CIDR (the NetBird
    default is the `100.64.0.0/10` CGNAT range), route and address conflicts are
    possible. Use distinct network ranges per account where you control them.
  - NetBird "routes" that advertise overlapping subnets, or a default-route
    (`0.0.0.0/0`) policy on both networks, will contend. Resolve with per-interface
    metrics or by disabling server routes on one instance (`netbird up
    --disable-server-routes`, set via the instance's config).
  - NetBird DNS management edits system resolver settings (NRPT rules on Windows).
    Two instances both managing DNS can conflict. Consider `--disable-dns` on one
    instance if you see resolution issues.
- Host-level isolation cannot be guaranteed by TwinBird. The two networks share
  one kernel routing table and one DNS stack. If strict isolation is required, run
  each account in a separate OS user, VM, or network namespace. TwinBird's
  contribution is to keep the instances' identities, configs, interfaces, and
  control channels separate; it does not sandbox traffic between them.

## Supply chain

- Runtime dependency surface is intentionally small (Typer only).
- `.github/dependabot.yml` enables weekly updates for pip dependencies and GitHub
  Actions.
- Release uses PyPI Trusted Publishing (OIDC), and workflow permissions are scoped
  per job (`contents: read` by default, `contents: write` only for the release
  job, `id-token: write` only for the publish job).
- Recommended follow-up: pin GitHub Actions to commit SHAs (Dependabot can then
  keep the pinned SHAs current) and commit a lockfile if TwinBird is consumed as
  an application rather than a library.

## Reporting

Report suspected vulnerabilities via the project's GitHub Security Advisories or
the issue tracker at https://github.com/Inzynx/TwinBird/issues.
