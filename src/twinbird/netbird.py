from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from twinbird.platform import (
    hidden_process_flags,
    managed_child_env,
    managed_netbird_home,
)


class OperationCancelled(Exception):
    """Raised when a cancellable NetBird CLI operation is stopped by the user."""


def managed_log_file(config_dir: Path) -> Path:
    """Path of the isolated daemon's log (used for startup-failure diagnostics)."""
    return managed_netbird_home(config_dir) / "daemon.log"


def managed_config_file(config_dir: Path) -> Path:
    return managed_netbird_home(config_dir) / "config.json"


def find_netbird_bin() -> str:
    bin_path = os.environ.get("TWINBIRD_NETBIRD_BIN", "netbird")
    if bin_path == "netbird" and sys.platform == "win32":
        # Prefer the local patched build when this editable source checkout has
        # one. The build output is ignored by git and optional for other users.
        build_dir = Path(__file__).resolve().parents[2] / "dist"
        for filename in ("netbird-twinbird.exe", "netbird-twinbird-0.64.2.exe"):
            local_build = build_dir / filename
            if local_build.is_file():
                bin_path = str(local_build)
                break
    if bin_path != "netbird":
        if not shutil.which(bin_path):
            msg = (
                f"netbird binary not found at '{bin_path}'"
                " (set via TWINBIRD_NETBIRD_BIN)."
            )
            raise FileNotFoundError(msg)
    elif shutil.which("netbird") is None:
        msg = "netbird not found on PATH. Install NetBird or set TWINBIRD_NETBIRD_BIN."
        raise FileNotFoundError(msg)
    return bin_path


def run_version(netbird_bin: str) -> subprocess.CompletedProcess[str]:
    """Return the installed NetBird client version without contacting a daemon."""
    return subprocess.run(
        [netbird_bin, "version"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=hidden_process_flags(),
        timeout=10,
    )


def run_service(
    netbird_bin: str,
    config_dir: Path,
    daemon_addr: str,
) -> subprocess.Popen[Any]:
    # Each instance's daemon gets an isolated data dir (own identity/state) via a
    # redirected %PROGRAMDATA% on Windows; without this NetBird shares one global
    # ProgramData\Netbird dir and instances collide. See platform.py.
    home = managed_netbird_home(config_dir)
    home.mkdir(parents=True, exist_ok=True)
    env = managed_child_env(config_dir)
    config_file = home / "config.json"
    log_file = home / "daemon.log"
    cmd = [
        netbird_bin,
        "service",
        "run",
        "--config",
        str(config_file),
        "--daemon-addr",
        daemon_addr,
        "--log-file",
        str(log_file),
    ]

    kwargs: dict[str, Any] = {"env": env}
    if sys.platform == "win32":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True

    # The daemon writes its own --log-file; also capture any pre-logging stdio
    # so a crash-on-startup banner is not lost. Close our handle promptly.
    with open(log_file, "w") as log_handle:
        if sys.platform != "win32":
            with contextlib.suppress(OSError):
                os.chmod(log_file, 0o600)
        return subprocess.Popen(cmd, stdout=log_handle, stderr=log_handle, **kwargs)


def _write_key_file(config_dir: Path, setup_key: str) -> Path:
    """Write the setup key to an owner-only file and return its path."""
    key_file = config_dir / ".setup-key"
    if sys.platform != "win32":
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(setup_key)
    else:
        key_file.write_text(setup_key)
    return key_file


def _run_cancellable(
    cmd: list[str],
    *,
    env: dict[str, str] | None,
    cancel_event: threading.Event,
) -> subprocess.CompletedProcess[str]:
    """Run a captured NetBird command and terminate it when cancellation is set."""
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        creationflags=hidden_process_flags(),
    )
    while True:
        if cancel_event.is_set():
            with contextlib.suppress(OSError):
                process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            raise OperationCancelled("NetBird connection was cancelled.")
        try:
            stdout, stderr = process.communicate(timeout=0.2)
            return subprocess.CompletedProcess(
                cmd, process.returncode or 0, stdout, stderr
            )
        except subprocess.TimeoutExpired:
            continue


def run_up(
    netbird_bin: str,
    daemon_addr: str,
    management_url: str,
    config_dir: Path,
    setup_key: str | None = None,
    interface_name: str | None = None,
    interactive: bool = True,
    wireguard_port: int | None = None,
    isolate: bool = True,
    cancel_event: threading.Event | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        netbird_bin,
        "up",
        "--daemon-addr",
        daemon_addr,
        "--management-url",
        management_url,
    ]
    if interface_name:
        cmd += ["--interface-name", interface_name]
    if wireguard_port:
        cmd += ["--wireguard-port", str(wireguard_port)]

    # Isolated instances target their own daemon's data dir; an adopted primary
    # must use the real NetBird data dir, so isolate=False there.
    env = managed_child_env(config_dir) if isolate else None

    if setup_key:
        # Pass the key via a file, never on the command line: process command
        # lines are readable by other local users (/proc, Win32_Process).
        key_file = _write_key_file(config_dir, setup_key)
        cmd += ["--setup-key-file", str(key_file)]
        try:
            if cancel_event is not None:
                return _run_cancellable(cmd, env=env, cancel_event=cancel_event)
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                env=env,
                creationflags=hidden_process_flags(),
            )
        finally:
            key_file.unlink(missing_ok=True)

    if not interactive:
        # OAuth flow driven from a GUI/service with no attached console: capture
        # the output (which includes the SSO verification URL) so the caller can
        # surface it instead of writing to a missing terminal.
        if cancel_event is not None:
            return _run_cancellable(cmd, env=env, cancel_event=cancel_event)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            creationflags=hidden_process_flags(),
        )

    # No setup key = interactive OAuth flow, let the user see the output.
    return subprocess.run(cmd, text=True, check=False, env=env)


def run_down(
    netbird_bin: str,
    daemon_addr: str,
) -> subprocess.CompletedProcess[str]:
    cmd = [netbird_bin, "down", "--daemon-addr", daemon_addr]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        creationflags=hidden_process_flags(),
        timeout=15,
    )


def run_status(
    netbird_bin: str,
    daemon_addr: str,
    *,
    json_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = [netbird_bin, "status", "--daemon-addr", daemon_addr]
    if json_output:
        cmd.append("--json")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        creationflags=hidden_process_flags(),
        timeout=8,
    )
