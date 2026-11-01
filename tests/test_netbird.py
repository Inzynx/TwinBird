from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from twinbird.netbird import find_netbird_bin


class TestFindNetbirdBin:
    def test_prefers_version_independent_build(self, tmp_path: Path) -> None:
        build_dir = tmp_path / "dist"
        build_dir.mkdir()
        current = build_dir / "netbird-twinbird.exe"
        current.touch()
        (build_dir / "netbird-twinbird-0.64.2.exe").touch()
        with (
            patch("twinbird.netbird.sys.platform", "win32"),
            patch(
                "twinbird.netbird.__file__", str(tmp_path / "src/twinbird/netbird.py")
            ),
            patch("shutil.which", side_effect=lambda name: name),
            patch.dict("os.environ", {}, clear=True),
        ):
            assert find_netbird_bin() == str(current)

    def test_keeps_legacy_build_as_fallback(self, tmp_path: Path) -> None:
        build_dir = tmp_path / "dist"
        build_dir.mkdir()
        legacy = build_dir / "netbird-twinbird-0.64.2.exe"
        legacy.touch()
        with (
            patch("twinbird.netbird.sys.platform", "win32"),
            patch(
                "twinbird.netbird.__file__", str(tmp_path / "src/twinbird/netbird.py")
            ),
            patch("shutil.which", side_effect=lambda name: name),
            patch.dict("os.environ", {}, clear=True),
        ):
            assert find_netbird_bin() == str(legacy)

    def test_found_on_path(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/netbird"),
            patch("pathlib.Path.is_file", return_value=False),
            patch.dict("os.environ", {}, clear=True),
        ):
            assert find_netbird_bin() == "netbird"

    def test_not_found(self) -> None:
        with (
            patch("shutil.which", return_value=None),
            patch("pathlib.Path.is_file", return_value=False),
            patch.dict("os.environ", {}, clear=True),
        ):
            try:
                find_netbird_bin()
                raise AssertionError("Should have raised FileNotFoundError")
            except FileNotFoundError as e:
                assert "TWINBIRD_NETBIRD_BIN" in str(e)

    def test_env_override(self) -> None:
        with (
            patch("shutil.which", return_value="/custom/netbird"),
            patch.dict("os.environ", {"TWINBIRD_NETBIRD_BIN": "/custom/netbird"}),
        ):
            assert find_netbird_bin() == "/custom/netbird"

    def test_env_override_not_found(self) -> None:
        with (
            patch("shutil.which", return_value=None),
            patch.dict("os.environ", {"TWINBIRD_NETBIRD_BIN": "/bad/path"}),
        ):
            try:
                find_netbird_bin()
                raise AssertionError("Should have raised FileNotFoundError")
            except FileNotFoundError as e:
                assert "TWINBIRD_NETBIRD_BIN" in str(e)


class TestRunUp:
    def test_refuses_to_switch_existing_managed_profile(self, tmp_path):
        from twinbird.netbird import run_up

        home = tmp_path / "Netbird"
        home.mkdir()
        state = home / "active_profile.json"
        state.write_text('{"name":"Important","username":"user"}')
        with (
            patch("twinbird.netbird.sys.platform", "win32"),
            patch("twinbird.netbird.subprocess.run") as run,
            pytest.raises(RuntimeError, match="non-default"),
        ):
            run_up("netbird", "tcp://127.0.0.1:42001", "https://example.com", tmp_path)
        run.assert_not_called()
        assert '"Important"' in state.read_text()

    def test_managed_windows_cli_profile_is_per_instance(self, tmp_path):
        from unittest.mock import MagicMock

        from twinbird.netbird import run_up

        original_appdata = str(tmp_path / "real-roaming")
        with (
            patch("twinbird.netbird.sys.platform", "win32"),
            patch.dict("os.environ", {"APPDATA": original_appdata}),
            patch(
                "twinbird.netbird.subprocess.run",
                return_value=MagicMock(returncode=0),
            ) as run,
        ):
            run_up("netbird", "tcp://127.0.0.1:42001", "https://example.com", tmp_path)

        assert run.call_args.kwargs["env"]["APPDATA"] == str(
            (tmp_path / "cli-profile").resolve()
        )
        assert original_appdata != run.call_args.kwargs["env"]["APPDATA"]

    def test_managed_linux_cli_profile_is_per_instance(self, tmp_path):
        from unittest.mock import MagicMock

        from twinbird.netbird import run_up

        with (
            patch("twinbird.netbird.sys.platform", "linux"),
            patch.dict("os.environ", {"XDG_CONFIG_HOME": "/real/config"}),
            patch(
                "twinbird.netbird.subprocess.run",
                return_value=MagicMock(returncode=0),
            ) as run,
        ):
            run_up("netbird", "unix:///tmp/test.sock", "https://example.com", tmp_path)

        assert run.call_args.kwargs["env"]["XDG_CONFIG_HOME"] == str(
            (tmp_path / "cli-profile").resolve()
        )

    def test_existing_primary_keeps_real_user_profile(self, tmp_path):
        from unittest.mock import MagicMock

        from twinbird.netbird import run_up

        with patch(
            "twinbird.netbird.subprocess.run",
            return_value=MagicMock(returncode=0),
        ) as run:
            run_up(
                "netbird",
                "tcp://127.0.0.1:41731",
                "https://example.com",
                tmp_path,
                isolate=False,
            )
        assert run.call_args.kwargs["env"] is None

    def test_setup_key_passed_as_file_not_argv(self, tmp_path):
        from unittest.mock import MagicMock

        from twinbird.netbird import run_up

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            # key file must exist at call time
            idx = cmd.index("--setup-key-file")
            captured["key_contents"] = Path(cmd[idx + 1]).read_text()
            return MagicMock(returncode=0, stdout="ok")

        with patch("twinbird.netbird.subprocess.run", side_effect=fake_run):
            run_up(
                "netbird",
                "npipe://twinbird-office",
                "https://mgmt.example.com",
                tmp_path,
                setup_key="SECRET-KEY",
                interface_name="wt7",
            )

        assert "--setup-key" not in captured["cmd"]  # never the raw flag
        assert "--setup-key-file" in captured["cmd"]
        assert "SECRET-KEY" not in captured["cmd"]  # key not on the argv
        assert captured["key_contents"] == "SECRET-KEY"
        # Key file is cleaned up afterwards
        assert not (tmp_path / ".setup-key").exists()

    def test_oauth_no_key_file(self, tmp_path):
        from unittest.mock import MagicMock

        from twinbird.netbird import run_up

        with patch(
            "twinbird.netbird.subprocess.run",
            return_value=MagicMock(returncode=0),
        ) as m:
            run_up(
                "netbird",
                "npipe://twinbird-office",
                "https://mgmt.example.com",
                tmp_path,
            )
        cmd = m.call_args[0][0]
        assert "--setup-key-file" not in cmd
        assert not (tmp_path / ".setup-key").exists()
