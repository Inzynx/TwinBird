from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import typer

from twinbird.daemon import is_daemon_reachable
from twinbird.platform import (
    hidden_process_flags,
    managed_child_env,
    managed_netbird_home,
)


def _task_name(name: str) -> str:
    return f"twinbird-{name}"


def _build_netbird_cmd(
    netbird_bin: str,
    config_dir: Path,
    daemon_addr: str,
    log_file: Path,
) -> list[str]:
    return [
        netbird_bin,
        "service",
        "run",
        "--config",
        str(managed_netbird_home(config_dir) / "config.json"),
        "--daemon-addr",
        daemon_addr,
        "--log-file",
        str(managed_netbird_home(config_dir) / "daemon.log")
        if sys.platform == "win32"
        else str(log_file),
    ]


def register_service(
    name: str,
    netbird_bin: str,
    config_dir: Path,
    daemon_addr: str,
    log_file: Path,
) -> None:
    if sys.platform == "win32":
        _register_windows(name, netbird_bin, config_dir, daemon_addr, log_file)
    elif sys.platform == "darwin":
        _register_macos(name, netbird_bin, config_dir, daemon_addr, log_file)
    else:
        _register_linux(name, netbird_bin, config_dir, daemon_addr, log_file)


def unregister_service(name: str) -> None:
    if sys.platform == "win32":
        _unregister_windows(name)
    elif sys.platform == "darwin":
        _unregister_macos(name)
    else:
        _unregister_linux(name)


def stop_registered_service(name: str) -> None:
    """Stop a service-launched daemon when its original PID is unavailable."""
    if sys.platform == "win32":
        subprocess.run(
            ["schtasks", "/end", "/tn", _task_name(name)],
            capture_output=True,
            text=True,
            check=False,
            creationflags=hidden_process_flags(),
            timeout=15,
        )
    elif sys.platform != "darwin":
        subprocess.run(
            ["systemctl", "--user", "stop", f"twinbird-{name}.service"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )


def is_service_registered(name: str) -> bool:
    if sys.platform == "win32":
        return _is_registered_windows(name)
    elif sys.platform == "darwin":
        return _is_registered_macos(name)
    else:
        return _is_registered_linux(name)


# --- Windows: Task Scheduler ---

_TASK_XML_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <RunLevel>HighestAvailable</RunLevel>
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Hidden>true</Hidden>
    <Enabled>true</Enabled>
  </Settings>
  <Actions>
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _request_elevation(command_line: str) -> None:
    """Run a command line with UAC elevation via PowerShell."""
    subprocess.run(
        [
            "powershell",
            "-Command",
            f"Start-Process cmd.exe -ArgumentList '/c {command_line}' "
            "-Verb RunAs -Wait -WindowStyle Hidden",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=hidden_process_flags(),
    )


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _write_task_xml(
    name: str, parts: list[str], environment: dict[str, str] | None = None
) -> Path:
    """Write a Task Scheduler XML definition to a temp file and return its path."""
    # Wrap in PowerShell to avoid a visible console window
    ps_args = " ".join(_powershell_quote(p) for p in parts)
    command = "powershell.exe"
    assignments = ""
    if environment:
        assignments = "; ".join(
            f"$env:{key}={_powershell_quote(value)}"
            for key, value in environment.items()
        )
        assignments += "; "
    arguments = f'-WindowStyle Hidden -NoProfile -Command "{assignments}& {ps_args}"'

    xml_content = _TASK_XML_TEMPLATE.format(
        command=xml_escape(command),
        arguments=xml_escape(arguments),
    )
    xml_file = Path(tempfile.gettempdir()) / f"twinbird-{name}.xml"
    xml_file.write_text(xml_content, encoding="utf-16")
    return xml_file


def _register_windows(
    name: str,
    netbird_bin: str,
    config_dir: Path,
    daemon_addr: str,
    log_file: Path,
) -> None:
    parts = _build_netbird_cmd(netbird_bin, config_dir, daemon_addr, log_file)
    task_name = _task_name(name)
    child_env = managed_child_env(config_dir)
    task_env = {
        key: child_env[key]
        for key in ("PROGRAMDATA", "NB_STATE_DIR", "TWINBIRD_WINTUN_GUID")
        if key in child_env
    }
    xml_file = _write_task_xml(name, parts, task_env)

    try:
        result = subprocess.run(
            ["schtasks", "/create", "/tn", task_name, "/xml", str(xml_file), "/f"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=hidden_process_flags(),
        )
        if result.returncode == 0:
            return

        typer.echo("Requesting administrator privileges to register startup task...")
        _request_elevation(f'schtasks /create /tn "{task_name}" /xml "{xml_file}" /f')

        if not _is_registered_windows(name):
            typer.echo(
                f"Warning: failed to register startup task for '{name}'.",
                err=True,
            )
    finally:
        xml_file.unlink(missing_ok=True)


def _unregister_windows(name: str) -> None:
    task_name = _task_name(name)
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", task_name, "/f"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=hidden_process_flags(),
    )
    if result.returncode == 0:
        return

    if _is_registered_windows(name):
        _request_elevation(f'schtasks /delete /tn "{task_name}" /f')


def _is_registered_windows(name: str) -> bool:
    result = subprocess.run(
        ["schtasks", "/query", "/tn", _task_name(name)],
        capture_output=True,
        text=True,
        check=False,
        creationflags=hidden_process_flags(),
        timeout=8,
    )
    return result.returncode == 0


# --- Linux: systemd user unit ---


def _systemd_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _systemd_unit_path(name: str) -> Path:
    return _systemd_unit_dir() / f"twinbird-{name}.service"


_SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description=Twinbird instance: {name}

[Service]
Environment={state_environment}
ExecStart={exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def _register_linux(
    name: str,
    netbird_bin: str,
    config_dir: Path,
    daemon_addr: str,
    log_file: Path,
) -> None:
    unit_dir = _systemd_unit_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)

    cmd_parts = _build_netbird_cmd(netbird_bin, config_dir, daemon_addr, log_file)
    exec_start = " ".join(shlex.quote(part) for part in cmd_parts)

    unit_path = _systemd_unit_path(name)
    unit_path.write_text(
        _SYSTEMD_UNIT_TEMPLATE.format(
            name=name,
            exec_start=exec_start,
            state_environment=shlex.quote(
                "NB_STATE_DIR=" + str(config_dir.resolve()).replace("%", "%%")
            ),
        )
    )

    reload_result = subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True,
        text=True,
        check=False,
    )
    enable_result = subprocess.run(
        ["systemctl", "--user", "enable", f"twinbird-{name}.service"],
        capture_output=True,
        text=True,
        check=False,
    )
    if reload_result.returncode != 0 or enable_result.returncode != 0:
        stderr = " | ".join(
            s for s in (reload_result.stderr, enable_result.stderr) if s
        )
        typer.echo(
            f"Warning: failed to register service for '{name}': {stderr}",
            err=True,
        )


def _unregister_linux(name: str) -> None:
    subprocess.run(
        ["systemctl", "--user", "disable", f"twinbird-{name}.service"],
        capture_output=True,
        text=True,
        check=False,
    )
    unit_path = _systemd_unit_path(name)
    if unit_path.exists():
        unit_path.unlink()
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True,
        text=True,
        check=False,
    )


def _is_registered_linux(name: str) -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-enabled", f"twinbird-{name}.service"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


# --- macOS: launchd user agent ---


def _launchd_plist_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _launchd_plist_path(name: str) -> Path:
    return _launchd_plist_dir() / f"com.twinbird.{name}.plist"


_LAUNCHD_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.twinbird.{name}</string>
    <key>ProgramArguments</key>
    <array>
{program_arguments}
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>NB_STATE_DIR</key>
        <string>{state_dir}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_file}</string>
    <key>StandardErrorPath</key>
    <string>{log_file}</string>
</dict>
</plist>
"""


def _register_macos(
    name: str,
    netbird_bin: str,
    config_dir: Path,
    daemon_addr: str,
    log_file: Path,
) -> None:
    plist_dir = _launchd_plist_dir()
    plist_dir.mkdir(parents=True, exist_ok=True)

    resolved_binary = shutil.which(netbird_bin) or netbird_bin
    cmd_parts = _build_netbird_cmd(resolved_binary, config_dir, daemon_addr, log_file)
    program_arguments = "\n".join(
        f"        <string>{xml_escape(part)}</string>" for part in cmd_parts
    )

    plist_path = _launchd_plist_path(name)
    plist_path.write_text(
        _LAUNCHD_PLIST_TEMPLATE.format(
            name=name,
            program_arguments=program_arguments,
            state_dir=xml_escape(str(config_dir.resolve())),
            log_file=xml_escape(str(log_file)),
        )
    )

    # The currently running daemon owns this socket. Loading a RunAtLoad job
    # immediately would start a second daemon against the same state directory.
    # A LaunchAgent in this directory will be discovered on the next login.
    if is_daemon_reachable(daemon_addr):
        return

    result = subprocess.run(
        ["launchctl", "load", str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        typer.echo(
            f"Warning: failed to register service for '{name}': {result.stderr}",
            err=True,
        )


def _unregister_macos(name: str) -> None:
    plist_path = _launchd_plist_path(name)
    if plist_path.exists():
        subprocess.run(
            ["launchctl", "unload", str(plist_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        plist_path.unlink()


def _is_registered_macos(name: str) -> bool:
    return _launchd_plist_path(name).exists()
