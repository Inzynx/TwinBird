from __future__ import annotations

import contextlib
import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

# Owner-only permissions for config dirs and secret-bearing files on POSIX.
# NetBird stores the WireGuard private key in config.json; metadata holds the
# management URL. On Windows, %APPDATA% already inherits a user-only ACL.
_DIR_MODE = 0o700
_FILE_MODE = 0o600


def _secure_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            path.chmod(_DIR_MODE)


def _secure_write_text(path: Path, data: str) -> None:
    """Write text, creating the file with owner-only perms on POSIX."""
    if sys.platform != "win32":
        # Open with O_CREAT|O_EXCL semantics is overkill here; ensure the mode
        # is applied even if the file already existed.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(path, flags, _FILE_MODE)
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(data)
        finally:
            with contextlib.suppress(OSError):
                path.chmod(_FILE_MODE)
    else:
        path.write_text(data)


@dataclass
class InstanceMetadata:
    name: str
    management_url: str
    daemon_addr: str
    interface_name: str
    pid: int
    created_at: str
    service_registered: bool = False
    wireguard_port: int | None = None
    # True when this entry adopts an existing (primary) NetBird managed by its own
    # service: TwinBird proxies status/up/down to its daemon but never starts its
    # own daemon, touches autostart, or deletes the primary's config.
    managed_externally: bool = False
    # A user-facing label; `name` remains the stable directory/service identity.
    display_name: str | None = None


def instance_dir(config_root: Path, name: str) -> Path:
    return config_root / name


def ensure_instance_dir(config_root: Path, name: str) -> Path:
    _secure_mkdir(config_root)
    path = instance_dir(config_root, name)
    _secure_mkdir(path)
    return path


def seed_netbird_config(config_dir: Path, interface_name: str) -> None:
    """Ensure the NetBird config.json has the correct WgIface before daemon start.

    Refuses to overwrite an existing config.json that cannot be parsed: that
    file may hold the WireGuard private key, and clobbering it would force a
    re-registration (and silently discard credentials).
    """
    from twinbird.platform import managed_netbird_home

    home = managed_netbird_home(config_dir)
    _secure_mkdir(home)
    config_path = home / "config.json"
    if config_path.exists():
        raw = config_path.read_text()
        if raw.strip():
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                msg = (
                    f"Existing NetBird config at {config_path} is not valid JSON; "
                    "refusing to overwrite it (it may contain the WireGuard "
                    "private key). Inspect or remove it manually."
                )
                raise RuntimeError(msg) from exc
        else:
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        msg = f"Existing NetBird config at {config_path} is not a JSON object."
        raise RuntimeError(msg)
    if data.get("WgIface") != interface_name:
        data["WgIface"] = interface_name
        _secure_write_text(config_path, json.dumps(data, indent=2))


def write_metadata(config_root: Path, metadata: InstanceMetadata) -> None:
    path = instance_dir(config_root, metadata.name) / "instance.json"
    _secure_write_text(path, json.dumps(asdict(metadata), indent=2))


def read_metadata(config_root: Path, name: str) -> InstanceMetadata | None:
    path = instance_dir(config_root, name) / "instance.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("service_registered", False)
    # Ignore unknown keys so a newer-written file still loads on older code.
    known = {f.name for f in fields(InstanceMetadata)}
    filtered = {k: v for k, v in data.items() if k in known}
    try:
        return InstanceMetadata(**filtered)
    except TypeError:
        return None


def list_instances(config_root: Path) -> list[str]:
    if not config_root.exists():
        return []
    return [
        d.name
        for d in sorted(config_root.iterdir())
        if d.is_dir() and (d / "instance.json").exists()
    ]


def pid_file_path(config_root: Path, name: str) -> Path:
    return instance_dir(config_root, name) / "daemon.pid"


def write_pid(config_root: Path, name: str, pid: int) -> None:
    _secure_write_text(pid_file_path(config_root, name), str(pid))


def read_pid(config_root: Path, name: str) -> int | None:
    path = pid_file_path(config_root, name)
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except ValueError:
        return None


def remove_pid(config_root: Path, name: str) -> None:
    path = pid_file_path(config_root, name)
    if path.exists():
        path.unlink()
