from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from twinbird.config import ensure_instance_dir, write_pid
from twinbird.daemon import is_process_alive, start_daemon, stop_daemon


class TestIsProcessAlive:
    def test_dead_process(self) -> None:
        assert is_process_alive(99999999) is False

    def test_alive_process(self) -> None:
        assert is_process_alive(os.getpid()) is True


class TestStartDaemon:
    def test_start_success(self, tmp_path: Path) -> None:
        ensure_instance_dir(tmp_path, "office")
        mock_proc = MagicMock()
        mock_proc.pid = 42

        with (
            patch("twinbird.daemon.netbird.run_service", return_value=mock_proc),
            patch("twinbird.daemon.is_process_alive", return_value=True),
        ):
            pid = start_daemon(
                netbird_bin="netbird",
                config_dir=tmp_path / "office",
                daemon_addr="tcp://127.0.0.1:52200",
                config_root=tmp_path,
                name="office",
            )
            assert pid == 42

    def test_start_failure(self, tmp_path: Path) -> None:
        ensure_instance_dir(tmp_path, "office")
        mock_proc = MagicMock()
        mock_proc.pid = 42

        with (
            patch("twinbird.daemon.netbird.run_service", return_value=mock_proc),
            patch("twinbird.daemon.is_process_alive", return_value=False),
            patch("twinbird.daemon.time.sleep"),
        ):
            try:
                start_daemon(
                    netbird_bin="netbird",
                    config_dir=tmp_path / "office",
                    daemon_addr="tcp://127.0.0.1:52200",
                    config_root=tmp_path,
                    name="office",
                )
                raise AssertionError("Should have raised RuntimeError")
            except RuntimeError as e:
                assert "office" in str(e)


class TestStopDaemon:
    def test_stop_no_pid(self, tmp_path: Path) -> None:
        ensure_instance_dir(tmp_path, "office")
        stop_daemon(tmp_path, "office")  # should not raise

    def test_stop_stale_pid(self, tmp_path: Path) -> None:
        ensure_instance_dir(tmp_path, "office")
        write_pid(tmp_path, "office", 99999999)
        with patch("twinbird.daemon.is_process_alive", return_value=False):
            stop_daemon(tmp_path, "office")
        from twinbird.config import read_pid

        assert read_pid(tmp_path, "office") is None


class TestIsDaemonProcess:
    def test_unknown_image_is_permitted(self) -> None:
        from twinbird.daemon import is_daemon_process

        with patch("twinbird.daemon._process_image_path", return_value=None):
            assert is_daemon_process(1234, "netbird") is True

    def test_netbird_image_matches(self) -> None:
        from twinbird.daemon import is_daemon_process

        with patch(
            "twinbird.daemon._process_image_path",
            return_value="/usr/bin/netbird",
        ):
            assert is_daemon_process(1234, "netbird") is True

    def test_foreign_image_rejected(self) -> None:
        from twinbird.daemon import is_daemon_process

        with patch(
            "twinbird.daemon._process_image_path",
            return_value="/usr/bin/sshd",
        ):
            assert is_daemon_process(1234, "netbird") is False


class TestStopDaemonPidReuse:
    def test_does_not_kill_recycled_pid(self, tmp_path: Path) -> None:
        ensure_instance_dir(tmp_path, "office")
        write_pid(tmp_path, "office", 4321)
        with (
            patch("twinbird.daemon.is_process_alive", return_value=True),
            patch("twinbird.daemon.is_daemon_process", return_value=False),
            patch("twinbird.daemon.os.kill") as mock_kill,
        ):
            stop_daemon(tmp_path, "office", netbird_bin="netbird")
        mock_kill.assert_not_called()
        from twinbird.config import read_pid

        assert read_pid(tmp_path, "office") is None


class TestReachable:
    def test_unix_missing_socket(self) -> None:
        from twinbird.daemon import is_daemon_reachable

        assert is_daemon_reachable("unix:///nonexistent/twinbird.sock") is False

    def test_npipe_absent(self) -> None:
        from twinbird.daemon import is_daemon_reachable

        with patch("twinbird.daemon.os.path.exists", return_value=False):
            assert is_daemon_reachable("npipe://twinbird-nope") is False

    def test_bad_scheme(self) -> None:
        from twinbird.daemon import is_daemon_reachable

        assert is_daemon_reachable("http://x") is False
