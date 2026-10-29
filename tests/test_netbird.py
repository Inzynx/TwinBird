from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from twinbird.netbird import find_netbird_bin


class TestFindNetbirdBin:
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
