from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from twinbird.platform import (
    InvalidInstanceName,
    PlatformConfig,
    derive_daemon_addr,
    derive_interface_name,
    get_platform_config,
    resolve_interface_name,
    validate_instance_name,
)


class TestGetPlatformConfig:
    def test_linux_defaults(self) -> None:
        with (
            patch("twinbird.platform.sys.platform", "linux"),
            patch("twinbird.platform.Path.home", return_value=Path("/home/user")),
            patch.dict("os.environ", {}, clear=True),
        ):
            config = get_platform_config()
            assert config.config_root == Path("/home/user/.config/twinbird")
            assert config.interface_prefix == "wt"

    def test_windows_defaults(self) -> None:
        with (
            patch("twinbird.platform.sys.platform", "win32"),
            patch.dict(
                "os.environ",
                {"APPDATA": "C:/Users/user/AppData/Roaming"},
                clear=True,
            ),
        ):
            config = get_platform_config()
            assert config.config_root == Path("C:/Users/user/AppData/Roaming/twinbird")
            assert config.interface_prefix == "wt"

    def test_darwin_defaults(self) -> None:
        with (
            patch("twinbird.platform.sys.platform", "darwin"),
            patch("twinbird.platform.Path.home", return_value=Path("/Users/user")),
            patch.dict("os.environ", {}, clear=True),
        ):
            config = get_platform_config()
            assert config.config_root == Path("/Users/user/.config/twinbird")
            assert config.interface_prefix == "utun"

    def test_env_override(self) -> None:
        with (
            patch("twinbird.platform.sys.platform", "linux"),
            patch.dict("os.environ", {"TWINBIRD_CONFIG_DIR": "/custom/path"}),
        ):
            config = get_platform_config()
            assert config.config_root == Path("/custom/path")


def test_linux_managed_state_is_separate(tmp_path: Path) -> None:
    from twinbird.platform import managed_child_env

    instance = tmp_path / "office"
    with (
        patch("twinbird.platform.sys.platform", "linux"),
        patch.dict(
            "os.environ",
            {
                "NB_STATE_DIR": "/var/lib/netbird",
                "TWINBIRD_SETUP_KEY": "test-secret",
            },
        ),
    ):
        env = managed_child_env(instance)
    assert env["NB_STATE_DIR"] == str(instance.resolve())
    assert "TWINBIRD_SETUP_KEY" not in env


class TestDeriveDaemonAddr:
    def test_windows_loopback_tcp(self) -> None:
        # The NetBird agent only supports [unix|tcp]:// for --daemon-addr (no
        # named pipes), so Windows uses a loopback TCP port, never 41731 (stock).
        config = PlatformConfig(
            config_root=Path("C:/Users/user/AppData/twinbird"),
            interface_prefix="wt",
        )
        with patch("twinbird.platform.sys.platform", "win32"):
            addr = derive_daemon_addr("office", config)
            assert addr.startswith("tcp://127.0.0.1:")
            port = int(addr.rsplit(":", 1)[1])
            assert 42000 <= port <= 42999
            assert port != 41731

    def test_deterministic(self) -> None:
        config = PlatformConfig(
            config_root=Path("/home/user/.config/twinbird"),
            interface_prefix="wt",
        )
        with patch("twinbird.platform.sys.platform", "win32"):
            assert derive_daemon_addr("office", config) == derive_daemon_addr(
                "office", config
            )

    def test_different_names_different_addrs(self) -> None:
        config = PlatformConfig(
            config_root=Path("/home/user/.config/twinbird"),
            interface_prefix="wt",
        )
        with patch("twinbird.platform.sys.platform", "win32"):
            assert derive_daemon_addr("office", config) != derive_daemon_addr(
                "home", config
            )

    def test_linux_root(self) -> None:
        config = PlatformConfig(
            config_root=Path("/home/user/.config/twinbird"),
            interface_prefix="wt",
        )
        with (
            patch("twinbird.platform.sys.platform", "linux"),
            patch("twinbird.platform._is_root", return_value=True),
        ):
            addr = derive_daemon_addr("office", config)
            assert addr == "unix:///var/run/twinbird-office.sock"

    def test_linux_non_root(self) -> None:
        config = PlatformConfig(
            config_root=Path("/home/user/.config/twinbird"),
            interface_prefix="wt",
        )
        with (
            patch("twinbird.platform.sys.platform", "linux"),
            patch("twinbird.platform._is_root", return_value=False),
        ):
            addr = derive_daemon_addr("office", config)
            expected = "unix:///home/user/.config/twinbird/office/office.sock"
            assert addr == expected


class TestDeriveInterfaceName:
    def test_linux_prefix(self) -> None:
        config = PlatformConfig(config_root=Path("/tmp"), interface_prefix="wt")
        name = derive_interface_name("office", config)
        assert name.startswith("wt")
        index = int(name[2:])
        assert 1 <= index <= 99

    def test_darwin_prefix(self) -> None:
        config = PlatformConfig(config_root=Path("/tmp"), interface_prefix="utun")
        name = derive_interface_name("office", config)
        assert name.startswith("utun")
        index = int(name[4:])
        assert 1 <= index <= 99

    def test_deterministic(self) -> None:
        config = PlatformConfig(config_root=Path("/tmp"), interface_prefix="wt")
        assert derive_interface_name("office", config) == derive_interface_name(
            "office", config
        )

    def test_different_names(self) -> None:
        config = PlatformConfig(config_root=Path("/tmp"), interface_prefix="wt")
        assert derive_interface_name("office", config) != derive_interface_name(
            "home", config
        )


class TestValidateInstanceName:
    def test_accepts_simple(self) -> None:
        assert validate_instance_name("office") == "office"
        assert validate_instance_name("work-01") == "work-01"
        assert validate_instance_name("a_b-9") == "a_b-9"

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "..",
            "../evil",
            "a/b",
            "a\b",
            "has space",
            "semi;colon",
            "quote'inject",
            "$(whoami)",
            "name\nnewline",
            "-leadingdash",
            "a" * 65,
            "con",
            "LPT1",
            "nul",
        ],
    )
    def test_rejects_bad(self, bad: str) -> None:
        with pytest.raises(InvalidInstanceName):
            validate_instance_name(bad)


class TestResolveInterfaceName:
    def test_matches_derive_when_free(self) -> None:
        config = PlatformConfig(config_root=Path("/tmp"), interface_prefix="wt")
        assert resolve_interface_name("office", config, set()) == derive_interface_name(
            "office", config
        )

    def test_avoids_taken(self) -> None:
        config = PlatformConfig(config_root=Path("/tmp"), interface_prefix="wt")
        first = derive_interface_name("office", config)
        resolved = resolve_interface_name("office", config, {first})
        assert resolved != first
        assert resolved.startswith("wt")

    def test_falls_back_when_all_taken(self) -> None:
        config = PlatformConfig(config_root=Path("/tmp"), interface_prefix="wt")
        taken = {f"wt{i}" for i in range(1, 100)}
        resolved = resolve_interface_name("office", config, taken)
        assert resolved == derive_interface_name("office", config)


class TestResolveWireguardPort:
    def test_deterministic_and_in_range(self) -> None:
        from twinbird.platform import derive_wireguard_port

        p = derive_wireguard_port("office")
        assert 51821 <= p <= 52820
        assert derive_wireguard_port("office") == p

    def test_avoids_default_and_taken(self) -> None:
        from twinbird.platform import (
            NETBIRD_DEFAULT_WG_PORT,
            derive_wireguard_port,
            resolve_wireguard_port,
        )

        first = derive_wireguard_port("office")
        resolved = resolve_wireguard_port("office", {first})
        assert resolved != first
        assert resolved != NETBIRD_DEFAULT_WG_PORT

    def test_never_returns_primary_default(self) -> None:
        from twinbird.platform import NETBIRD_DEFAULT_WG_PORT, resolve_wireguard_port

        # Even if every other port were taken, 51820 must never be handed out.
        got = resolve_wireguard_port("x", set())
        assert got != NETBIRD_DEFAULT_WG_PORT


class TestResolveControlPort:
    def test_in_range_and_avoids_default(self) -> None:
        from twinbird.platform import (
            NETBIRD_DEFAULT_DAEMON_PORT,
            derive_control_port,
            resolve_control_port,
        )

        p = derive_control_port("office")
        assert 42000 <= p <= 42999
        assert resolve_control_port("office", {p}) != p
        assert resolve_control_port("x", set()) != NETBIRD_DEFAULT_DAEMON_PORT


class TestDefaultPrimaryDaemonAddr:
    def test_windows_tcp_41731(self) -> None:
        from twinbird.platform import default_primary_daemon_addr

        with patch("twinbird.platform.sys.platform", "win32"):
            assert default_primary_daemon_addr() == "tcp://127.0.0.1:41731"

    def test_posix_unix_socket(self) -> None:
        from twinbird.platform import default_primary_daemon_addr

        with patch("twinbird.platform.sys.platform", "linux"):
            assert default_primary_daemon_addr() == "unix:///var/run/netbird.sock"
