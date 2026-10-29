from __future__ import annotations

import hashlib
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

# Instance names become directory names, named-pipe names, systemd unit names,
# Task Scheduler task names and are interpolated into shell/XML. Restrict them to
# a conservative, injection- and traversal-proof character set.
_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Reserved Windows device names that must never become a directory/pipe name.
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class InvalidInstanceName(ValueError):
    """Raised when an instance name fails validation."""


def validate_instance_name(name: str) -> str:
    """Validate and return an instance name, or raise InvalidInstanceName.

    Guards every downstream use of the name (filesystem paths, pipe/socket
    names, systemd units, scheduled-task names, shell/XML interpolation).
    """
    if not _VALID_NAME.match(name):
        msg = (
            f"Invalid instance name {name!r}. Use 1-64 characters: ASCII "
            "letters, digits, '-' or '_', starting with a letter or digit."
        )
        raise InvalidInstanceName(msg)
    if name.lower() in _WINDOWS_RESERVED:
        msg = f"Instance name {name!r} is a reserved device name."
        raise InvalidInstanceName(msg)
    return name


@dataclass(frozen=True)
class PlatformConfig:
    config_root: Path
    interface_prefix: str


def _is_root() -> bool:
    if hasattr(os, "getuid"):
        return os.getuid() == 0  # type: ignore[attr-defined]
    return False


def get_platform_config() -> PlatformConfig:
    override = os.environ.get("TWINBIRD_CONFIG_DIR")
    if override:
        config_root = Path(override)
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        config_root = Path(appdata if appdata else str(Path.home())) / "twinbird"
    else:
        config_root = Path.home() / ".config" / "twinbird"

    interface_prefix = "utun" if sys.platform == "darwin" else "wt"

    return PlatformConfig(config_root=config_root, interface_prefix=interface_prefix)


# NetBird's daemon control channel. The agent only accepts `[unix|tcp]://` for
# --daemon-addr (named pipes are NOT supported by the client), and the stock
# daemon listens on tcp://127.0.0.1:41731. On Windows TwinBird therefore uses a
# loopback TCP port per instance; on Linux/macOS a per-instance Unix socket.
NETBIRD_DEFAULT_DAEMON_PORT = 41731
_CTRL_PORT_BASE = 42000
_CTRL_PORT_SPAN = 1000


def derive_control_port(name: str) -> int:
    h = int(hashlib.sha256(("ctrl" + name).encode()).hexdigest(), 16)
    return _CTRL_PORT_BASE + (h % _CTRL_PORT_SPAN)


def resolve_control_port(name: str, taken: set[int]) -> int:
    """Return a free daemon control port avoiding ``taken`` and the stock 41731."""
    blocked = set(taken) | {NETBIRD_DEFAULT_DAEMON_PORT}
    start = derive_control_port(name) - _CTRL_PORT_BASE
    for offset in range(_CTRL_PORT_SPAN):
        port = _CTRL_PORT_BASE + ((start + offset) % _CTRL_PORT_SPAN)
        if port not in blocked:
            return port
    return derive_control_port(name)


def derive_daemon_addr(name: str, config: PlatformConfig) -> str:
    """Derive the per-instance daemon control address.

    Windows: a loopback TCP port (the NetBird agent does not support named pipes
    for --daemon-addr). It is bound to 127.0.0.1 only. The NetBird daemon gRPC
    server has no authentication, so treat the loopback port as reachable by any
    local process - this matches the stock NetBird daemon's own model. See
    SECURITY.md.
    """
    if sys.platform == "win32":
        return f"tcp://127.0.0.1:{derive_control_port(name)}"

    if _is_root():
        return f"unix:///var/run/twinbird-{name}.sock"

    sock_path = config.config_root / name / f"{name}.sock"
    return f"unix://{sock_path.as_posix()}"


def managed_netbird_home(config_dir: Path) -> Path:
    """Directory the isolated NetBird daemon uses for its state/profile files.

    On Windows NetBird derives its data directory from %PROGRAMDATA% (always
    appending "Netbird"), ignoring --config for active_profile.json/state.json.
    To isolate each instance we point %PROGRAMDATA% at the instance dir, so its
    files land in ``<config_dir>/Netbird``. On other platforms the instance dir
    is used directly.
    """
    if sys.platform == "win32":
        return config_dir / "Netbird"
    return config_dir


def managed_child_env(config_dir: Path) -> dict[str, str]:
    """Environment for an isolated daemon: redirects NetBird's data dir."""
    env = dict(os.environ)
    # The setup key is consumed by TwinBird and written to a temporary file for
    # `netbird up`; never pass its environment variable to the long-lived daemon.
    env.pop("TWINBIRD_SETUP_KEY", None)
    if sys.platform != "win32":
        # NetBird 0.64 uses this for its profile and state files as well as
        # --config. Without it, separate daemons share /var/lib/netbird.
        env["NB_STATE_DIR"] = str(config_dir.resolve())
    if sys.platform == "win32":
        env["PROGRAMDATA"] = str(config_dir)
        env["NB_STATE_DIR"] = str(managed_netbird_home(config_dir).resolve())
        # Stock NetBird 0.64.2 ignores this variable and keeps its fixed Wintun
        # GUID. TwinBird-compatible builds read it to give each interface a
        # stable, per-instance adapter GUID.
        instance_path = str(config_dir.resolve()).casefold()
        instance_guid = uuid.uuid5(
            uuid.NAMESPACE_URL, f"twinbird-wintun:{instance_path}"
        )
        env["TWINBIRD_WINTUN_GUID"] = f"{{{instance_guid}}}"
    return env


def hidden_process_flags() -> int:
    """Hide non-interactive console subprocesses on Windows."""
    return 0x08000000 if sys.platform == "win32" else 0


def default_primary_daemon_addr() -> str:
    """The control address the stock NetBird daemon/service uses by default.

    Used when adopting an already-installed NetBird so TwinBird talks to its
    existing daemon instead of starting a second one.
    """
    if sys.platform == "win32":
        return f"tcp://127.0.0.1:{NETBIRD_DEFAULT_DAEMON_PORT}"
    return "unix:///var/run/netbird.sock"


def _interface_index(name: str) -> int:
    h = int(hashlib.sha256(name.encode()).hexdigest(), 16)
    return 1 + (h % 99)


def derive_interface_name(name: str, config: PlatformConfig) -> str:
    return f"{config.interface_prefix}{_interface_index(name)}"


def resolve_interface_name(
    name: str,
    config: PlatformConfig,
    taken: set[str],
) -> str:
    """Return a WireGuard interface name for ``name`` avoiding ``taken``.

    Starts from the deterministic hash-derived name and linear-probes the
    1..99 index space so two instances whose hashes collide still get distinct
    interfaces (a collision would otherwise make the second daemon fail or
    hijack the first interface).
    """
    start = _interface_index(name)
    for offset in range(99):
        index = 1 + ((start - 1 + offset) % 99)
        candidate = f"{config.interface_prefix}{index}"
        if candidate not in taken:
            return candidate
    # All 99 slots taken (pathological); fall back to the deterministic name.
    return f"{config.interface_prefix}{start}"


# WireGuard listen port. NetBird's default is 51820; two interfaces cannot share
# one UDP port, so each TwinBird instance gets its own (above the default) and we
# avoid the primary NetBird's port to keep the existing install stable.
NETBIRD_DEFAULT_WG_PORT = 51820
_WG_PORT_BASE = 51821
_WG_PORT_SPAN = 1000


def derive_wireguard_port(name: str) -> int:
    h = int(hashlib.sha256(("wg" + name).encode()).hexdigest(), 16)
    return _WG_PORT_BASE + (h % _WG_PORT_SPAN)


def resolve_wireguard_port(name: str, taken: set[int]) -> int:
    """Return a free WireGuard port for ``name`` avoiding ``taken``.

    ``taken`` should include the primary NetBird's port and every other
    instance's port so no two interfaces bind the same UDP port.
    """
    blocked = set(taken) | {NETBIRD_DEFAULT_WG_PORT}
    start = derive_wireguard_port(name) - _WG_PORT_BASE
    for offset in range(_WG_PORT_SPAN):
        port = _WG_PORT_BASE + ((start + offset) % _WG_PORT_SPAN)
        if port not in blocked:
            return port
    return derive_wireguard_port(name)
