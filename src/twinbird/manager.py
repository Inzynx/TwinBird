"""GUI/CLI-agnostic orchestration layer.

This module holds the single source of truth for instance lifecycle logic. The
Typer CLI (``instance.py``) and the desktop GUI/tray both call these functions.
Nothing here depends on Typer or prints to a terminal: progress is delivered
through an ``emit`` callback and failures are raised as :class:`ManagerError`.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from twinbird.config import (
    InstanceMetadata,
    ensure_instance_dir,
    instance_dir,
    list_instances,
    read_metadata,
    read_pid,
    remove_pid,
    seed_netbird_config,
    write_metadata,
)
from twinbird.daemon import (
    is_daemon_process,
    is_daemon_reachable,
    is_process_alive,
    start_daemon,
    stop_daemon,
)
from twinbird.detect import detect_primary
from twinbird.netbird import (
    OperationCancelled,
    find_netbird_bin,
    managed_log_file,
    run_down,
    run_status,
    run_up,
    run_version,
)
from twinbird.platform import (
    InvalidInstanceName,
    PlatformConfig,
    default_primary_daemon_addr,
    derive_daemon_addr,
    get_platform_config,
    resolve_control_port,
    resolve_interface_name,
    resolve_wireguard_port,
    validate_instance_name,
)
from twinbird.service import (
    is_service_registered,
    register_service,
    stop_registered_service,
    unregister_service,
)

Emit = Callable[[str], None]


def _noop(_: str) -> None:
    pass


class ManagerError(Exception):
    """A user-facing lifecycle error (already carries a readable message)."""


@dataclass(frozen=True)
class InstanceStatus:
    name: str
    running: bool
    reachable: bool
    persistent: bool
    pid: int | None
    interface_name: str | None
    management_url: str | None
    daemon_addr: str | None
    managed_externally: bool = False
    netbird_ip: str | None = None
    peer_name: str | None = None
    management_connected: bool | None = None
    display_name: str | None = None
    wireguard_port: int | None = None

    @property
    def label(self) -> str:
        return self.display_name or self.name

    @property
    def up(self) -> bool:
        return self.running or self.reachable

    @property
    def can_start(self) -> bool:
        # An adopted service stays reachable after `netbird down`. Reconnecting
        # it must remain possible, including when its status query failed.
        return not self.up or (
            self.managed_externally and self.management_connected is not True
        )

    @property
    def can_stop(self) -> bool:
        return self.up and not (
            self.managed_externally and self.management_connected is False
        )

    @property
    def autostart_label(self) -> str:
        if self.managed_externally:
            return "service"
        return "on" if self.persistent else "off"

    @property
    def state_label(self) -> str:
        if not self.up:
            return "stopped"
        if self.managed_externally:
            if self.management_connected is False:
                return "disconnected (existing)"
            return "running (existing)"
        return "running (persistent)" if self.persistent else "running"


def _require_valid(name: str) -> str:
    try:
        return validate_instance_name(name)
    except InvalidInstanceName as exc:
        raise ManagerError(str(exc)) from exc


def _interfaces_in_use(platform: PlatformConfig, exclude: str) -> set[str]:
    """Interfaces used by other instances, plus the primary NetBird's."""
    taken: set[str] = set()
    for other in list_instances(platform.config_root):
        if other == exclude:
            continue
        meta = read_metadata(platform.config_root, other)
        if meta:
            taken.add(meta.interface_name)
    primary = detect_primary()
    if primary and primary.wg_iface:
        taken.add(primary.wg_iface)
    return taken


def _control_ports_in_use(platform: PlatformConfig, exclude: str) -> set[int]:
    """TCP control ports used by other instances (from their daemon_addr)."""
    taken: set[int] = set()
    for other in list_instances(platform.config_root):
        if other == exclude:
            continue
        meta = read_metadata(platform.config_root, other)
        if meta:
            parsed = urlparse(meta.daemon_addr)
            if parsed.scheme == "tcp" and parsed.port:
                taken.add(parsed.port)
    return taken


def _wg_ports_in_use(platform: PlatformConfig, exclude: str) -> set[int]:
    """WireGuard ports used by other instances, plus the primary NetBird's."""
    taken: set[int] = set()
    for other in list_instances(platform.config_root):
        if other == exclude:
            continue
        meta = read_metadata(platform.config_root, other)
        if meta and meta.wireguard_port:
            taken.add(meta.wireguard_port)
    primary = detect_primary()
    if primary and primary.wg_port:
        taken.add(primary.wg_port)
    return taken


def _interface_creation_collision(
    config_dir: Path, interface_name: str, detail: str
) -> bool:
    """Identify a failed TUN adapter creation, including daemon-side errors."""
    evidence = detail.lower()
    with contextlib.suppress(OSError):
        evidence += (
            "\n"
            + managed_log_file(config_dir)
            .read_text(encoding="utf-8", errors="replace")
            .lower()
        )
    return (
        f"interface {interface_name.lower()}" in evidence
        and ("already exists" in evidence or "file already exists" in evidence)
    ) or "failed to initiate stub device creation" in evidence


def start(
    name: str,
    management_url: str,
    setup_key: str | None = None,
    interface_name: str | None = None,
    daemon_addr: str | None = None,
    *,
    interactive: bool = True,
    emit: Emit = _noop,
    cancel_event: threading.Event | None = None,
) -> None:
    """Start (or adopt) an instance and register it for autostart.

    Raises :class:`ManagerError` on failure. Callers read the resulting state via
    :func:`get_status` / :func:`list_statuses`.
    """
    _require_valid(name)
    if not management_url:
        raise ManagerError("A management URL is required.")

    netbird_bin = find_netbird_bin()
    platform = get_platform_config()
    config_dir = ensure_instance_dir(platform.config_root, name)

    existing = read_metadata(platform.config_root, name)
    if existing and existing.managed_externally:
        # Adopted primary NetBird: just (re)connect its own daemon; never start a
        # second daemon, seed config, or register autostart.
        emit(f"Connecting existing NetBird '{name}'...")
        try:
            result = run_up(
                netbird_bin,
                existing.daemon_addr,
                existing.management_url,
                config_dir,
                setup_key,
                None,
                interactive=interactive,
                isolate=False,  # use the real NetBird data dir, not a TwinBird copy
                cancel_event=cancel_event,
            )
        except OperationCancelled as exc:
            raise ManagerError("Connection attempt cancelled.") from exc
        if getattr(result, "stdout", None):
            emit(result.stdout.rstrip())
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise ManagerError(detail or "Failed to connect existing NetBird.")
        emit(f"Existing NetBird '{name}' is up.")
        return

    if sys.platform == "win32" and netbird_bin == "netbird":
        version = run_version(netbird_bin)
        if version.returncode == 0 and version.stdout.strip() == "0.64.2":
            existing_interfaces = _interfaces_in_use(platform, exclude=name)
            if existing_interfaces:
                raise ManagerError(
                    "Cannot start another Windows NetBird instance with stock "
                    "NetBird 0.64.2 while an interface is already configured. "
                    "That client requests one fixed Wintun GUID for every "
                    "interface, so a second adapter fails even when TwinBird "
                    "chooses a different name and runs as administrator. This "
                    "check stops before sending the setup key. Use a NetBird "
                    "build that assigns a unique Wintun GUID per instance. "
                    "TwinBird provides scripts/build-netbird-windows.ps1; set "
                    "TWINBIRD_NETBIRD_BIN to its output and restart TwinBird."
                )

    if daemon_addr:
        resolved_addr = daemon_addr
    elif existing:
        resolved_addr = existing.daemon_addr  # stable across restarts
    elif sys.platform == "win32":
        port = resolve_control_port(name, _control_ports_in_use(platform, name))
        resolved_addr = f"tcp://127.0.0.1:{port}"
    else:
        resolved_addr = derive_daemon_addr(name, platform)

    if interface_name:
        resolved_iface = interface_name
    elif existing:
        resolved_iface = existing.interface_name
    else:
        taken = _interfaces_in_use(platform, exclude=name)
        resolved_iface = resolve_interface_name(name, platform, taken)

    # Each WireGuard interface needs its own UDP port; reuse the instance's own
    # port across restarts and avoid the primary NetBird and other instances.
    if existing and existing.wireguard_port:
        resolved_port = existing.wireguard_port
    else:
        resolved_port = resolve_wireguard_port(
            name, _wg_ports_in_use(platform, exclude=name)
        )

    log_file = config_dir / "daemon.log"

    pid = read_pid(platform.config_root, name)
    if pid and is_process_alive(pid):
        metadata = read_metadata(platform.config_root, name)
        if metadata:
            if not is_service_registered(name):
                register_service(
                    name=name,
                    netbird_bin=netbird_bin,
                    config_dir=config_dir,
                    daemon_addr=metadata.daemon_addr,
                    log_file=log_file,
                )
                metadata.service_registered = True
                write_metadata(platform.config_root, metadata)
            emit(f"Instance '{name}' is already running (PID {pid}).")
            return
        emit(f"Cleaning up orphaned daemon (PID {pid})...")
        stop_daemon(platform.config_root, name, netbird_bin)

    if pid:
        remove_pid(platform.config_root, name)

    seed_netbird_config(config_dir, resolved_iface)

    emit(f"Starting daemon for instance '{name}'...")
    try:
        daemon_pid = start_daemon(
            netbird_bin=netbird_bin,
            config_dir=config_dir,
            daemon_addr=resolved_addr,
            config_root=platform.config_root,
            name=name,
        )
    except RuntimeError as exc:
        raise ManagerError(str(exc)) from exc

    emit(f"Connecting to {management_url}...")
    try:
        result = run_up(
            netbird_bin,
            resolved_addr,
            management_url,
            config_dir,
            setup_key,
            resolved_iface,
            interactive=interactive,
            wireguard_port=resolved_port,
            cancel_event=cancel_event,
        )
    except OperationCancelled as exc:
        stop_daemon(platform.config_root, name, netbird_bin)
        raise ManagerError(
            "Connection attempt cancelled. The temporary daemon was stopped."
        ) from exc
    if getattr(result, "stdout", None):
        emit(result.stdout.rstrip())
    detail = ""
    adapter_collision = False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        adapter_collision = _interface_creation_collision(
            config_dir, resolved_iface, detail
        )

    if result.returncode != 0:
        # Roll back the daemon we just started so we do not leak a process.
        daemon_log = ""
        with contextlib.suppress(OSError):
            daemon_log = (
                managed_log_file(config_dir)
                .read_text(encoding="utf-8", errors="replace")
                .lower()
            )
        stop_daemon(platform.config_root, name, netbird_bin)
        message = f"Failed to connect: {detail}" if detail else "Failed to connect."
        low = detail.lower()
        if sys.platform == "win32" and "error loading wintun.dll" in daemon_log:
            message += (
                "\nThe NetBird client could not load wintun.dll. Place a compatible "
                "wintun.dll next to the NetBird client executable and retry."
            )
        elif adapter_collision and sys.platform == "win32":
            message += (
                "\nCause: NetBird 0.64.2's Windows client requests the same fixed "
                "Wintun adapter GUID for every instance. Changing the adapter "
                "name cannot avoid that GUID collision. Running a second "
                "instance requires a NetBird build that assigns a unique GUID "
                "per instance; administrator rights do not resolve this error."
            )
        elif "access is denied" in low or "access denied" in low:
            message += (
                "\nHint: creating the network adapter requires administrator "
                "rights. Start TwinBird as administrator and try again."
            )
        raise ManagerError(message)

    register_service(
        name=name,
        netbird_bin=netbird_bin,
        config_dir=config_dir,
        daemon_addr=resolved_addr,
        log_file=log_file,
    )

    metadata = InstanceMetadata(
        name=name,
        management_url=management_url,
        daemon_addr=resolved_addr,
        interface_name=resolved_iface,
        pid=daemon_pid,
        created_at=datetime.now(timezone.utc).isoformat(),
        service_registered=True,
        wireguard_port=resolved_port,
        display_name=existing.display_name if existing else None,
    )
    write_metadata(platform.config_root, metadata)
    emit(
        f"Instance '{name}' is up (PID {daemon_pid}, interface {resolved_iface}, "
        f"port {resolved_port})."
    )


def stop(name: str, *, emit: Emit = _noop) -> None:
    """Disconnect and stop an instance; also removes it from autostart."""
    _require_valid(name)
    platform = get_platform_config()
    metadata = read_metadata(platform.config_root, name)
    if metadata is None:
        raise ManagerError(f"Instance '{name}' not found.")

    netbird_bin = find_netbird_bin()

    if metadata.managed_externally:
        # Adopted primary: only disconnect via its daemon. Never kill the service
        # process or touch autostart.
        emit(f"Disconnecting existing NetBird '{name}'...")
        result = run_down(netbird_bin, metadata.daemon_addr)
        if result.returncode != 0:
            emit(f"Warning: disconnect failed: {result.stderr}")
        emit(f"Existing NetBird '{name}' is down.")
        return

    pid = read_pid(platform.config_root, name)

    if pid is None:
        emit(f"No daemon PID found for '{name}'; stopping its startup service...")
        if is_daemon_reachable(metadata.daemon_addr):
            run_down(netbird_bin, metadata.daemon_addr)
        stop_registered_service(name)
        unregister_service(name)
        emit(f"Instance '{name}' is down.")
        return

    if not is_process_alive(pid):
        remove_pid(platform.config_root, name)
        if is_daemon_reachable(metadata.daemon_addr):
            run_down(netbird_bin, metadata.daemon_addr)
        stop_registered_service(name)
        unregister_service(name)
        emit(f"Instance '{name}' is not running (stale PID cleaned up).")
        return

    unregister_service(name)
    emit(f"Disconnecting instance '{name}'...")
    result = run_down(netbird_bin, metadata.daemon_addr)
    if result.returncode != 0:
        emit(f"Warning: disconnect failed: {result.stderr}")

    emit(f"Stopping daemon (PID {pid})...")
    stop_daemon(platform.config_root, name, netbird_bin)
    emit(f"Instance '{name}' is down.")


def set_autostart(name: str, enabled: bool, *, emit: Emit = _noop) -> None:
    """Enable or disable start-on-boot for an existing instance."""
    _require_valid(name)
    platform = get_platform_config()
    metadata = read_metadata(platform.config_root, name)
    if metadata is None:
        raise ManagerError(f"Instance '{name}' not found.")
    if metadata.managed_externally:
        raise ManagerError(
            f"'{name}' is your existing NetBird; its autostart is managed by the "
            "NetBird service, not TwinBird."
        )

    config_dir = instance_dir(platform.config_root, name)
    if enabled:
        netbird_bin = find_netbird_bin()
        register_service(
            name=name,
            netbird_bin=netbird_bin,
            config_dir=config_dir,
            daemon_addr=metadata.daemon_addr,
            log_file=config_dir / "daemon.log",
        )
        metadata.service_registered = True
        emit(f"Autostart enabled for '{name}'.")
    else:
        unregister_service(name)
        metadata.service_registered = False
        emit(f"Autostart disabled for '{name}'.")
    write_metadata(platform.config_root, metadata)


def rename(name: str, label: str, *, emit: Emit = _noop) -> None:
    """Change the local label without moving identity files or service paths."""
    _require_valid(name)
    label = label.strip()
    if not label or len(label) > 64 or not label.isprintable():
        raise ManagerError("Use a name of 1-64 printable characters.")
    platform = get_platform_config()
    metadata = read_metadata(platform.config_root, name)
    if metadata is None:
        raise ManagerError(f"Instance '{name}' not found.")
    for other in list_instances(platform.config_root):
        if other == name:
            continue
        existing = read_metadata(platform.config_root, other)
        if (
            existing
            and (existing.display_name or existing.name).casefold() == label.casefold()
        ):
            raise ManagerError(f"An instance named '{label}' already exists.")
    old_label = metadata.display_name or name
    metadata.display_name = label if label != name else None
    write_metadata(platform.config_root, metadata)
    emit(f"Renamed '{old_label}' to '{label}'.")


def remove(name: str, *, emit: Emit = _noop) -> None:
    """Remove a managed instance locally; adopted primary entries are protected."""
    _require_valid(name)
    platform = get_platform_config()
    config_dir = instance_dir(platform.config_root, name)
    if not (config_dir / "instance.json").exists():
        raise ManagerError(f"Instance '{name}' not found.")

    metadata = read_metadata(platform.config_root, name)
    if metadata is None:
        raise ManagerError(f"Cannot read instance '{name}' metadata; removal stopped.")
    if metadata.managed_externally:
        raise ManagerError(
            f"'{name}' is your existing NetBird connection and cannot be removed "
            "from TwinBird."
        )

    tracked_pid = read_pid(platform.config_root, name)
    try:
        stop(name, emit=emit)
        process_alive = False
        for _ in range(10):
            process_alive = bool(
                tracked_pid
                and is_process_alive(tracked_pid)
                and is_daemon_process(tracked_pid, find_netbird_bin())
            )
            if not process_alive and not is_daemon_reachable(metadata.daemon_addr):
                break
            time.sleep(0.2)
        if process_alive:
            raise ManagerError(
                f"The daemon for '{name}' is still running. Its files were kept; "
                "stop the daemon and try again."
            )
        if is_daemon_reachable(metadata.daemon_addr):
            raise ManagerError(
                f"The daemon for '{name}' is still reachable. Its files were kept; "
                "stop the daemon and try again."
            )
        if is_service_registered(name):
            raise ManagerError(
                f"Autostart for '{name}' is still registered. Its files were kept; "
                "remove the startup entry and try again."
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManagerError(f"Could not stop '{name}': {exc}") from exc

    try:
        shutil.rmtree(config_dir)
    except OSError as exc:
        raise ManagerError(f"Could not remove local files for '{name}': {exc}") from exc
    emit(f"Instance '{name}' removed.")


def get_status(name: str) -> InstanceStatus | None:
    _require_valid(name)
    platform = get_platform_config()
    metadata = read_metadata(platform.config_root, name)
    if metadata is None:
        return None
    external = metadata.managed_externally
    if external:
        # Adopted primary: no TwinBird PID; reachability of its daemon is the
        # signal, and persistence is owned by the NetBird service.
        running = False
        reachable = is_daemon_reachable(metadata.daemon_addr)
        persistent = True
        pid = None
    else:
        pid = read_pid(platform.config_root, name)
        running = pid is not None and is_process_alive(pid)
        reachable = not running and is_daemon_reachable(metadata.daemon_addr)
        persistent = is_service_registered(name)
        pid = pid if running else None
    netbird_ip = None
    peer_name = None
    management_connected = None
    if (running or reachable) and metadata.daemon_addr:
        with contextlib.suppress(Exception):
            status_result = run_status(
                find_netbird_bin(), metadata.daemon_addr, json_output=True
            )
            if status_result.returncode == 0:
                details = json.loads(status_result.stdout)
                if isinstance(details, dict):
                    raw_ip = details.get("netbirdIp")
                    raw_name = details.get("fqdn")
                    management = details.get("management")
                    if isinstance(raw_ip, str):
                        netbird_ip = raw_ip.split("/", maxsplit=1)[0]
                    if isinstance(raw_name, str):
                        peer_name = raw_name
                    if isinstance(management, dict):
                        connected = management.get("connected")
                        if isinstance(connected, bool):
                            management_connected = connected

    return InstanceStatus(
        name=name,
        running=running,
        reachable=reachable,
        persistent=persistent,
        pid=pid,
        interface_name=metadata.interface_name,
        management_url=metadata.management_url,
        daemon_addr=metadata.daemon_addr,
        managed_externally=external,
        netbird_ip=netbird_ip,
        peer_name=peer_name,
        management_connected=management_connected,
        display_name=metadata.display_name,
        wireguard_port=metadata.wireguard_port,
    )


def list_statuses() -> list[InstanceStatus]:
    platform = get_platform_config()
    result: list[InstanceStatus] = []
    for name in list_instances(platform.config_root):
        status = get_status(name)
        if status is not None:
            result.append(status)
    return result


def status_text(name: str) -> str:
    """Return NetBird's own status output for a running instance, or a note."""
    status = get_status(name)
    if status is None:
        raise ManagerError(f"Instance '{name}' not found.")
    if not status.up or status.daemon_addr is None:
        return f"{name}: stopped"
    netbird_bin = find_netbird_bin()
    result = run_status(netbird_bin, status.daemon_addr)
    return result.stdout or result.stderr or ""


def _find_adopted_name(platform: PlatformConfig, daemon_addr: str) -> str | None:
    """Return the name of an already-adopted primary (same daemon addr), if any."""
    for existing in list_instances(platform.config_root):
        meta = read_metadata(platform.config_root, existing)
        if meta and meta.managed_externally and meta.daemon_addr == daemon_addr:
            return existing
    return None


def import_primary(name: str = "primary", *, emit: Emit = _noop) -> str:
    """Adopt the existing (primary) NetBird so it shows up as an instance.

    Read-only toward the primary: TwinBird records a pointer to its running daemon
    and never starts a second daemon, re-authenticates, or edits its config.
    Idempotent: if the primary is already adopted, returns the existing name
    instead of adding a duplicate. Returns the instance name used.
    """
    _require_valid(name)
    platform = get_platform_config()

    primary = detect_primary()
    if primary is None and sys.platform != "win32":
        raise ManagerError(
            "No existing NetBird installation was found in this Linux/macOS "
            "environment. Scan only adopts a NetBird already installed here; "
            "it cannot see the Windows installation from WSL. To create a new "
            "peer, use Add instance with a setup key."
        )
    if primary is None or primary.management_url is None:
        raise ManagerError(
            "Could not read a usable existing NetBird configuration. On Windows "
            "the config is usually readable only by administrators; run TwinBird "
            "as administrator and scan again, or add an instance manually."
        )

    daemon_addr = default_primary_daemon_addr()

    already = _find_adopted_name(platform, daemon_addr)
    if already is not None:
        emit(f"Existing NetBird already integrated as '{already}'.")
        return already

    # Pick a free name if the requested one is taken by a different instance.
    chosen = name
    suffix = 1
    while read_metadata(platform.config_root, chosen) is not None:
        suffix += 1
        chosen = f"{name}-{suffix}"
        _require_valid(chosen)

    ensure_instance_dir(platform.config_root, chosen)
    metadata = InstanceMetadata(
        name=chosen,
        management_url=primary.management_url,
        daemon_addr=daemon_addr,
        interface_name=primary.wg_iface or "wt0",
        pid=0,
        created_at=datetime.now(timezone.utc).isoformat(),
        service_registered=False,
        wireguard_port=primary.wg_port,
        managed_externally=True,
    )
    write_metadata(platform.config_root, metadata)
    emit(
        f"Integrated your existing NetBird as '{chosen}' "
        f"(server {primary.management_url}, adapter {metadata.interface_name})."
    )
    return chosen
