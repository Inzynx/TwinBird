"""Start two isolated, unauthenticated Linux daemons for a local smoke check.

Usage: python scripts/linux-smoke.py /path/to/netbird
No `up` command is sent and no VPN interface is requested.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twinbird.config import seed_netbird_config
from twinbird.daemon import is_daemon_reachable
from twinbird.netbird import run_service, run_status
from twinbird.platform import managed_child_env


def main() -> None:
    if not sys.platform.startswith("linux") or len(sys.argv) != 2:
        raise SystemExit(
            "Usage on Linux: python scripts/linux-smoke.py /path/to/netbird"
        )
    binary = os.path.abspath(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="twinbird-smoke-") as temporary:
        root = Path(temporary)
        processes: list[subprocess.Popen] = []
        addresses: list[str] = []
        try:
            for index, name in enumerate(("one", "two"), start=1):
                directory = root / name
                directory.mkdir()
                address = f"unix://{directory / 'daemon.sock'}"
                assert managed_child_env(directory)["NB_STATE_DIR"] == str(directory)
                seed_netbird_config(directory, f"wt{index}")
                addresses.append(address)
                process = run_service(binary, directory, address)
                processes.append(process)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(
                            f"{name} daemon exited: {process.returncode}"
                        )
                    if is_daemon_reachable(address):
                        break
                    time.sleep(0.2)
                else:
                    raise RuntimeError(f"{name} daemon did not expose its socket")
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("A daemon exited before both were ready")
            for name, address in zip(("one", "two"), addresses, strict=True):
                status = run_status(binary, address, json_output=True)
                if status.returncode != 0:
                    raise RuntimeError(
                        f"{name} status query failed: {status.stderr or status.stdout}"
                    )
                details = json.loads(status.stdout)
                if not isinstance(details, dict):
                    raise RuntimeError(f"{name} returned invalid status JSON")
                print(f"{name}: isolated daemon, state and status query ready")
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
