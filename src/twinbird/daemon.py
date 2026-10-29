from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from twinbird import netbird
from twinbird.config import read_pid, remove_pid, write_pid


def is_daemon_reachable(daemon_addr: str) -> bool:
    """Best-effort check that a daemon is serving on ``daemon_addr``.

    Used to detect a daemon started by the persistent service when no live PID
    is tracked in this process. Handles tcp, unix and npipe schemes.
    """
    parsed = urlparse(daemon_addr)
    scheme = parsed.scheme

    if scheme == "tcp":
        if not parsed.hostname or not parsed.port:
            return False
        try:
            with socket.create_connection((parsed.hostname, parsed.port), timeout=1):
                return True
        except OSError:
            return False

    if scheme == "unix":
        sock_path = daemon_addr[len("unix://") :]
        if not sock_path:
            return False
        if not Path(sock_path).exists():
            return False
        af_unix = getattr(socket, "AF_UNIX", None)
        if af_unix is None:
            # No AF_UNIX (e.g. Windows): existence of the socket file is the
            # best signal available.
            return True
        try:
            with socket.socket(af_unix, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                sock.connect(sock_path)
                return True
        except OSError:
            return False

    if scheme == "npipe":
        pipe_name = daemon_addr[len("npipe://") :]
        if not pipe_name:
            return False
        # NetBird serves either the plain pipe or the admin protected-namespace
        # pipe depending on elevation; presence of either means reachable.
        candidates = [
            rf"\\.\pipe\{pipe_name}",
            rf"\\.\pipe\ProtectedPrefix\Administrators\{pipe_name}",
        ]
        return any(os.path.exists(p) for p in candidates)

    return False


def is_process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return exit_code.value == STILL_ACTIVE

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_image_path(pid: int) -> str | None:
    """Return the executable path for ``pid``, or None if unknown."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            buf_len = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(buf_len.value)
            if kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(buf_len)
            ):
                return buf.value
            return None
        finally:
            kernel32.CloseHandle(handle)

    # Linux (and anything else exposing /proc/<pid>/exe).
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def is_daemon_process(pid: int, netbird_bin: str) -> bool:
    """Heuristically confirm ``pid`` is a NetBird daemon, not a recycled PID.

    Returns True when the image path cannot be determined (best effort — do not
    block a legitimate stop), but returns False when it is positively something
    else, so we never terminate an unrelated recycled PID.
    """
    image = _process_image_path(pid)
    if image is None:
        return True
    base = os.path.basename(image).lower()
    if base.startswith("netbird"):
        return True
    # Match an explicit binary path override too.
    return os.path.basename(netbird_bin).lower() in base


def start_daemon(
    netbird_bin: str,
    config_dir: Path,
    daemon_addr: str,
    config_root: Path,
    name: str,
) -> int:
    proc = netbird.run_service(netbird_bin, config_dir, daemon_addr)
    write_pid(config_root, name, proc.pid)

    for _ in range(10):
        time.sleep(0.5)
        if not is_process_alive(proc.pid):
            remove_pid(config_root, name)
            log_file = netbird.managed_log_file(config_dir)
            log_tail = ""
            if log_file.exists():
                lines = log_file.read_text().splitlines()
                log_tail = "\n".join(lines[-10:])
            msg = f"Daemon failed to start for instance '{name}'."
            if log_tail:
                msg += f"\n{log_tail}"
            raise RuntimeError(msg)

    return proc.pid


def stop_daemon(config_root: Path, name: str, netbird_bin: str | None = None) -> None:
    pid = read_pid(config_root, name)
    if pid is None:
        return

    if not is_process_alive(pid):
        remove_pid(config_root, name)
        return

    # Guard against PID reuse: if the live PID is demonstrably not a NetBird
    # daemon, treat the record as stale rather than killing an unrelated process.
    if netbird_bin is not None and not is_daemon_process(pid, netbird_bin):
        remove_pid(config_root, name)
        return

    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_TERMINATE = 0x0001
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
        if handle:
            try:
                if not kernel32.TerminateProcess(handle, 1):
                    raise OSError(f"Could not terminate NetBird daemon PID {pid}.")
                if kernel32.WaitForSingleObject(handle, 5000) != 0:
                    raise OSError(f"NetBird daemon PID {pid} did not stop in time.")
            finally:
                kernel32.CloseHandle(handle)
        elif is_process_alive(pid):
            raise OSError(f"Could not open NetBird daemon PID {pid} for termination.")
    else:
        import signal

        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            time.sleep(0.5)
            if not is_process_alive(pid):
                break
        else:
            os.kill(pid, signal.SIGKILL)

    remove_pid(config_root, name)
