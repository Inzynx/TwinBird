"""Read-only detection of an existing (primary) NetBird installation.

TwinBird never modifies the primary NetBird config. This module only *reads* it,
so the GUI/CLI can pre-fill the correct management URL and so TwinBird can avoid
colliding with the primary's WireGuard interface and port.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PrimaryNetbird:
    config_path: Path
    management_url: str | None
    admin_url: str | None
    wg_iface: str | None
    wg_port: int | None


def primary_config_candidates() -> list[Path]:
    """Candidate paths for the primary NetBird config/profile, newest name first."""
    if sys.platform == "win32":
        base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        roots = [base / "Netbird", base / "netbird"]
    elif sys.platform == "darwin":
        roots = [Path("/var/lib/netbird"), Path("/etc/netbird")]
    else:
        roots = [Path("/var/lib/netbird"), Path("/etc/netbird")]

    names = ["default.json", "config.json"]
    return [root / name for root in roots for name in names]


def _url_from_field(value: Any) -> str | None:
    """Rebuild a URL from NetBird's object form, or accept a plain string."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        scheme = value.get("Scheme") or value.get("scheme")
        host = value.get("Host") or value.get("host")
        if scheme and host:
            return f"{scheme}://{host}"
        if host:
            return str(host)
    return None


def parse_primary_config(path: Path) -> PrimaryNetbird | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    wg_port = data.get("WgPort")
    if not isinstance(wg_port, int):
        wg_port = None

    return PrimaryNetbird(
        config_path=path,
        management_url=_url_from_field(data.get("ManagementURL")),
        admin_url=_url_from_field(data.get("AdminURL")),
        wg_iface=data.get("WgIface") if isinstance(data.get("WgIface"), str) else None,
        wg_port=wg_port,
    )


def detect_primary() -> PrimaryNetbird | None:
    """Return info about the primary NetBird install, or None if not found.

    Best-effort and read-only. On Windows the primary config in ProgramData is
    often ACL-restricted to SYSTEM/Administrators, so even ``path.exists()`` can
    raise ``PermissionError``; that is treated as "not accessible".
    """
    for path in primary_config_candidates():
        try:
            exists = path.exists()
        except OSError:
            continue
        if exists:
            parsed = parse_primary_config(path)
            if parsed is not None:
                return parsed
            # File exists but unreadable/garbage: report its path only.
            return PrimaryNetbird(path, None, None, None, None)
    return None


def detected_management_url() -> str | None:
    """Convenience: the primary's management URL, if detectable."""
    primary = detect_primary()
    return primary.management_url if primary else None
