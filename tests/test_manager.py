from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from twinbird import manager
from twinbird.config import (
    InstanceMetadata,
    ensure_instance_dir,
    read_metadata,
    write_metadata,
    write_pid,
)
from twinbird.manager import ManagerError
from twinbird.platform import PlatformConfig


def _platform(tmp_path: Path) -> PlatformConfig:
    return PlatformConfig(config_root=tmp_path, interface_prefix="wt")


class TestStartValidation:
    def test_rejects_bad_name(self, tmp_path: Path) -> None:
        with (
            patch(
                "twinbird.manager.get_platform_config", return_value=_platform(tmp_path)
            ),
            pytest.raises(ManagerError),
        ):
            manager.start("../evil", "https://m.example.com")

    def test_requires_management_url(self, tmp_path: Path) -> None:
        with (
            patch(
                "twinbird.manager.get_platform_config", return_value=_platform(tmp_path)
            ),
            pytest.raises(ManagerError),
        ):
            manager.start("office", "")

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows Wintun diagnostic")
    def test_timeout_reports_missing_wintun_from_daemon_log(
        self, tmp_path: Path
    ) -> None:
        log_dir = tmp_path / "office" / "Netbird"
        log_dir.mkdir(parents=True)
        (log_dir / "daemon.log").write_text(
            "Error loading wintun.dll DLL: The specified module could not be found."
        )
        with (
            patch(
                "twinbird.manager.get_platform_config", return_value=_platform(tmp_path)
            ),
            patch("twinbird.manager.find_netbird_bin", return_value="custom.exe"),
            patch("twinbird.manager._interfaces_in_use", return_value=set()),
            patch("twinbird.manager._wg_ports_in_use", return_value=set()),
            patch("twinbird.manager.read_pid", return_value=None),
            patch("twinbird.manager.start_daemon", return_value=123),
            patch("twinbird.manager.stop_daemon") as stop,
            patch(
                "twinbird.manager.run_up",
                return_value=MagicMock(
                    returncode=1, stdout="", stderr="rpc error: DeadlineExceeded"
                ),
            ),
            pytest.raises(ManagerError, match=r"could not load wintun\.dll"),
        ):
            manager.start("office", "https://mgmt.example.com")
        stop.assert_called_once()


class TestMultipleInstances:
    def test_four_instances_get_distinct_resources(self, tmp_path: Path) -> None:
        platform = _platform(tmp_path)
        pids = iter(range(100, 200))

        with (
            patch("twinbird.manager.get_platform_config", return_value=platform),
            patch("twinbird.manager.find_netbird_bin", return_value="custom.exe"),
            patch("twinbird.manager.read_pid", return_value=None),
            patch(
                "twinbird.manager.start_daemon",
                side_effect=lambda **_: next(pids),
            ),
            patch(
                "twinbird.manager.run_up",
                return_value=MagicMock(returncode=0, stdout="Connected"),
            ),
            patch("twinbird.manager.register_service"),
        ):
            for name in ("alpha", "bravo", "charlie", "delta"):
                manager.start(name, "https://mgmt.example.com", setup_key="K")

        names = ("alpha", "bravo", "charlie", "delta")
        metas = [read_metadata(tmp_path, n) for n in names]
        assert all(m is not None for m in metas)
        interfaces = {m.interface_name for m in metas}  # type: ignore[union-attr]
        addrs = {m.daemon_addr for m in metas}  # type: ignore[union-attr]
        # Four instances, four distinct interfaces and four distinct control addrs.
        assert len(interfaces) == 4
        assert len(addrs) == 4


class TestStatus:
    def test_list_and_get_status(self, tmp_path: Path) -> None:
        platform = _platform(tmp_path)
        ensure_instance_dir(tmp_path, "office")
        write_metadata(
            tmp_path,
            InstanceMetadata(
                "office", "https://u", "tcp://127.0.0.1:42050", "wt7", 42, "t", True
            ),
        )
        write_pid(tmp_path, "office", 42)

        with (
            patch("twinbird.manager.get_platform_config", return_value=platform),
            patch("twinbird.manager.is_process_alive", return_value=True),
            patch("twinbird.manager.is_service_registered", return_value=True),
            patch(
                "twinbird.manager.run_status",
                return_value=MagicMock(returncode=1),
            ),
        ):
            statuses = manager.list_statuses()
            one = manager.get_status("office")

        assert len(statuses) == 1
        assert one is not None
        assert one.running is True
        assert one.persistent is True
        assert one.up is True
        assert one.state_label == "running (persistent)"

    def test_management_connection_is_read_from_status(self, tmp_path: Path) -> None:
        ensure_instance_dir(tmp_path, "office")
        write_metadata(
            tmp_path,
            InstanceMetadata(
                "office", "https://u", "tcp://127.0.0.1:42050", "wt7", 42, "t", True
            ),
        )
        write_pid(tmp_path, "office", 42)
        with (
            patch(
                "twinbird.manager.get_platform_config", return_value=_platform(tmp_path)
            ),
            patch("twinbird.manager.is_process_alive", return_value=True),
            patch("twinbird.manager.is_service_registered", return_value=True),
            patch("twinbird.manager.find_netbird_bin", return_value="netbird"),
            patch(
                "twinbird.manager.run_status",
                return_value=MagicMock(
                    returncode=0,
                    stdout='{"management":{"connected":true},"netbirdIp":"100.1.2.3/16"}',
                ),
            ),
        ):
            status = manager.get_status("office")
        assert status is not None
        assert status.management_connected is True
        assert status.netbird_ip == "100.1.2.3"

    def test_get_status_missing(self, tmp_path: Path) -> None:
        with patch(
            "twinbird.manager.get_platform_config", return_value=_platform(tmp_path)
        ):
            assert manager.get_status("nope") is None


class TestAutostart:
    def test_enable_then_disable(self, tmp_path: Path) -> None:
        platform = _platform(tmp_path)
        ensure_instance_dir(tmp_path, "office")
        write_metadata(
            tmp_path,
            InstanceMetadata(
                "office", "u", "tcp://127.0.0.1:42050", "wt7", 1, "t", False
            ),
        )

        with (
            patch("twinbird.manager.get_platform_config", return_value=platform),
            patch("twinbird.manager.find_netbird_bin", return_value="netbird"),
            patch("twinbird.manager.register_service") as reg,
            patch("twinbird.manager.unregister_service") as unreg,
        ):
            manager.set_autostart("office", True)
            assert reg.called
            assert read_metadata(tmp_path, "office").service_registered is True  # type: ignore[union-attr]

            manager.set_autostart("office", False)
            assert unreg.called
            assert read_metadata(tmp_path, "office").service_registered is False  # type: ignore[union-attr]

    def test_autostart_missing_instance(self, tmp_path: Path) -> None:
        with (
            patch(
                "twinbird.manager.get_platform_config", return_value=_platform(tmp_path)
            ),
            pytest.raises(ManagerError),
        ):
            manager.set_autostart("nope", True)


class TestRemove:
    def test_removes_state(self, tmp_path: Path) -> None:
        platform = _platform(tmp_path)
        ensure_instance_dir(tmp_path, "office")
        write_metadata(
            tmp_path,
            InstanceMetadata("office", "u", "a", "wt7", 1, "t", False),
        )

        with (
            patch("twinbird.manager.get_platform_config", return_value=platform),
            patch("twinbird.manager.stop") as stop_mock,
            patch("twinbird.manager.is_daemon_reachable", return_value=False),
            patch("twinbird.manager.is_service_registered", return_value=False),
        ):
            manager.remove("office")

        stop_mock.assert_called_once()
        assert not (tmp_path / "office").exists()

    def test_retains_files_when_daemon_still_running(self, tmp_path: Path) -> None:
        ensure_instance_dir(tmp_path, "office")
        write_metadata(tmp_path, InstanceMetadata("office", "u", "a", "wt7", 1, "t"))
        with (
            patch(
                "twinbird.manager.get_platform_config", return_value=_platform(tmp_path)
            ),
            patch("twinbird.manager.stop"),
            patch("twinbird.manager.is_daemon_reachable", return_value=True),
            patch("twinbird.manager.time.sleep"),
            pytest.raises(ManagerError, match="still reachable"),
        ):
            manager.remove("office")
        assert (tmp_path / "office" / "instance.json").exists()

    def test_reports_local_file_removal_failure(self, tmp_path: Path) -> None:
        ensure_instance_dir(tmp_path, "office")
        write_metadata(tmp_path, InstanceMetadata("office", "u", "a", "wt7", 1, "t"))
        with (
            patch(
                "twinbird.manager.get_platform_config", return_value=_platform(tmp_path)
            ),
            patch("twinbird.manager.stop"),
            patch("twinbird.manager.is_daemon_reachable", return_value=False),
            patch("twinbird.manager.is_service_registered", return_value=False),
            patch(
                "twinbird.manager.shutil.rmtree", side_effect=PermissionError("denied")
            ),
            pytest.raises(ManagerError, match="Could not remove local files"),
        ):
            manager.remove("office")
        assert (tmp_path / "office" / "instance.json").exists()

    def test_remove_missing(self, tmp_path: Path) -> None:
        with (
            patch(
                "twinbird.manager.get_platform_config", return_value=_platform(tmp_path)
            ),
            pytest.raises(ManagerError),
        ):
            manager.remove("nope")

    def test_remove_service_started_daemon_without_pid(self, tmp_path: Path) -> None:
        ensure_instance_dir(tmp_path, "office")
        write_metadata(tmp_path, InstanceMetadata("office", "u", "a", "wt7", 1, "t"))
        with (
            patch(
                "twinbird.manager.get_platform_config", return_value=_platform(tmp_path)
            ),
            patch("twinbird.manager.find_netbird_bin", return_value="netbird"),
            patch(
                "twinbird.manager.is_daemon_reachable", side_effect=[True, False, False]
            ),
            patch(
                "twinbird.manager.run_down", return_value=MagicMock(returncode=0)
            ) as down,
            patch("twinbird.manager.stop_registered_service") as stop_service,
            patch("twinbird.manager.unregister_service") as unregister,
            patch("twinbird.manager.is_service_registered", return_value=False),
        ):
            manager.remove("office")
        down.assert_called_once_with("netbird", "a")
        stop_service.assert_called_once_with("office")
        unregister.assert_called_once_with("office")
        assert not (tmp_path / "office").exists()


class TestImportPrimary:
    def _primary(self):
        from pathlib import Path as _P

        from twinbird.detect import PrimaryNetbird

        return PrimaryNetbird(
            config_path=_P("C:/ProgramData/Netbird/default.json"),
            management_url="https://api.netbird.io:443",
            admin_url="https://app.netbird.io:443",
            wg_iface="wt0",
            wg_port=51820,
        )

    def test_adopts_and_is_idempotent(self, tmp_path: Path) -> None:
        platform = _platform(tmp_path)
        with (
            patch("twinbird.manager.get_platform_config", return_value=platform),
            patch("twinbird.manager.detect_primary", return_value=self._primary()),
            patch(
                "twinbird.manager.default_primary_daemon_addr",
                return_value="tcp://127.0.0.1:41731",
            ),
        ):
            name1 = manager.import_primary("primary")
            name2 = manager.import_primary("primary")  # second scan

        assert name1 == "primary"
        assert name2 == "primary"  # no duplicate
        meta = read_metadata(tmp_path, "primary")
        assert meta is not None
        assert meta.managed_externally is True
        assert meta.daemon_addr == "tcp://127.0.0.1:41731"
        assert meta.management_url == "https://api.netbird.io:443"
        assert meta.interface_name == "wt0"
        # Only one instance exists.
        from twinbird.config import list_instances

        assert list_instances(tmp_path) == ["primary"]

    def test_raises_when_not_detectable(self, tmp_path: Path) -> None:
        platform = _platform(tmp_path)
        with (
            patch("twinbird.manager.get_platform_config", return_value=platform),
            patch("twinbird.manager.detect_primary", return_value=None),
            pytest.raises(ManagerError),
        ):
            manager.import_primary()

    def test_linux_scan_explains_no_local_netbird(self, tmp_path: Path) -> None:
        with (
            patch(
                "twinbird.manager.get_platform_config", return_value=_platform(tmp_path)
            ),
            patch("twinbird.manager.sys.platform", "linux"),
            patch("twinbird.manager.detect_primary", return_value=None),
            pytest.raises(
                ManagerError, match="cannot see the Windows installation from WSL"
            ),
        ):
            manager.import_primary()


class TestExternalGating:
    def _adopt(self, tmp_path: Path) -> None:
        ensure_instance_dir(tmp_path, "primary")
        write_metadata(
            tmp_path,
            InstanceMetadata(
                "primary",
                "https://api.netbird.io:443",
                "tcp://127.0.0.1:41731",
                "wt0",
                0,
                "t",
                service_registered=False,
                wireguard_port=51820,
                managed_externally=True,
            ),
        )

    def test_stop_proxies_without_killing(self, tmp_path: Path) -> None:
        platform = _platform(tmp_path)
        self._adopt(tmp_path)
        with (
            patch("twinbird.manager.get_platform_config", return_value=platform),
            patch("twinbird.manager.find_netbird_bin", return_value="netbird"),
            patch(
                "twinbird.manager.run_down",
                return_value=MagicMock(returncode=0),
            ) as down,
            patch("twinbird.manager.stop_daemon") as sd,
            patch("twinbird.manager.unregister_service") as unreg,
        ):
            manager.stop("primary")
        down.assert_called_once()
        sd.assert_not_called()
        unreg.assert_not_called()

    def test_autostart_rejected(self, tmp_path: Path) -> None:
        platform = _platform(tmp_path)
        self._adopt(tmp_path)
        with (
            patch("twinbird.manager.get_platform_config", return_value=platform),
            pytest.raises(ManagerError),
        ):
            manager.set_autostart("primary", True)

    def test_remove_protects_primary(self, tmp_path: Path) -> None:
        platform = _platform(tmp_path)
        self._adopt(tmp_path)
        with (
            patch("twinbird.manager.get_platform_config", return_value=platform),
            patch("twinbird.manager.stop") as stop_mock,
            patch("twinbird.manager.run_down") as down,
            pytest.raises(ManagerError, match="cannot be removed"),
        ):
            manager.remove("primary")
        stop_mock.assert_not_called()  # never disconnect the primary
        down.assert_not_called()
        assert (tmp_path / "primary" / "instance.json").exists()
