from __future__ import annotations

import importlib.util
from unittest.mock import MagicMock, patch

import pytest

# tkinter ships with CPython but can be absent on a minimal runner. The GUI module
# raises at import time without it, so skip these tests rather than fail CI.
pytest.importorskip("tkinter")


class TestGuiModule:
    def test_imports(self) -> None:
        # Importing the module must not require a display or the tray deps.
        import twinbird.gui as gui

        assert hasattr(gui, "run_gui")
        assert hasattr(gui, "TwinBirdApp")

    def test_windows_gui_launch_detaches_from_terminal(self) -> None:
        import os
        import subprocess

        from twinbird import gui

        with (
            patch("twinbird.gui.sys.platform", "win32"),
            patch("twinbird.gui.sys.argv", [r"C:\TwinBird\twinbird.exe", "gui"]),
            patch.dict(os.environ, {}, clear=True),
            patch("twinbird.gui.subprocess.Popen") as popen,
        ):
            gui.run_gui()

        args, kwargs = popen.call_args
        assert args[0] == [r"C:\TwinBird\twinbird.exe", "gui"]
        assert kwargs["creationflags"] == 0x00000008 | 0x00000200
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["env"]["TWINBIRD_GUI_CHILD"] == "1"

    def test_tray_availability_is_bool(self) -> None:
        import twinbird.gui as gui

        assert isinstance(gui._tray_available(), bool)

    def test_wsl_uses_window_without_tray(self) -> None:
        from twinbird import gui

        with (
            patch("twinbird.gui.sys.platform", "linux"),
            patch(
                "twinbird.gui.host_platform.release",
                return_value="6.6.1-Microsoft-WSL2",
            ),
        ):
            assert gui._is_wsl()
            assert not gui._tray_available()

    @pytest.mark.skipif(
        importlib.util.find_spec("PIL") is None,
        reason="Pillow not installed",
    )
    def test_tray_image_builds(self) -> None:
        import twinbird.gui as gui

        img = gui._make_tray_image()
        assert img.size == (64, 64)

    def test_refresh_is_one_background_snapshot(self) -> None:
        from twinbird.gui import TwinBirdApp

        app = TwinBirdApp.__new__(TwinBirdApp)
        app._busy = False
        app._refreshing = False
        app._last_checked_at = None
        app._checked_var = MagicMock()
        app._set_all_buttons = MagicMock()
        app._apply_statuses = MagicMock()
        callbacks = []
        app._post = callbacks.append

        def thread(*, target, daemon):
            return MagicMock(start=target)

        with (
            patch("twinbird.gui.threading.Thread", side_effect=thread),
            patch("twinbird.gui.manager.list_statuses", return_value=[]) as check,
        ):
            app.refresh()
            app.refresh()  # A pending refresh does not trigger a second check.
            check.assert_called_once()
            assert app._refreshing
            callbacks.pop()()

        assert not app._refreshing
        app._apply_statuses.assert_called_once_with([])
        assert app._checked_var.set.call_args.args[0].startswith("Last checked:")

    @pytest.mark.parametrize(
        ("connected", "start_state", "stop_state"),
        [
            (True, "disabled", "normal"),
            (False, "normal", "disabled"),
            (None, "normal", "normal"),
        ],
    )
    def test_primary_buttons_follow_connection_not_service(
        self, connected, start_state, stop_state
    ) -> None:
        from twinbird.gui import TwinBirdApp
        from twinbird.manager import InstanceStatus

        app = TwinBirdApp.__new__(TwinBirdApp)
        app._busy = app._refreshing = False
        for name in (
            "add",
            "scan",
            "refresh",
            "start",
            "stop",
            "auto",
            "remove",
            "rename",
        ):
            setattr(app, f"_btn_{name}", MagicMock())
        app._selected_name = lambda: "primary"
        status = InstanceStatus(
            "primary",
            False,
            True,
            True,
            None,
            "wt0",
            "u",
            "a",
            managed_externally=True,
            management_connected=connected,
        )
        app._status_by_name = {"primary": status}
        app._on_start = MagicMock()
        app._on_stop = MagicMock()
        app._update_buttons()
        app._btn_start.configure.assert_called_with(state=start_state)
        app._btn_stop.configure.assert_called_with(state=stop_state)
        app._btn_remove.configure.assert_called_with(state="disabled")
        app._on_row_double_click(None)
        if connected is False:
            app._on_start.assert_called_once()
            app._on_stop.assert_not_called()
            assert status.state_label == "disconnected (existing)"
        else:
            app._on_stop.assert_called_once()

    def test_remove_button_protects_adopted_instance(self) -> None:
        from twinbird.gui import TwinBirdApp
        from twinbird.manager import InstanceStatus

        app = TwinBirdApp.__new__(TwinBirdApp)
        app._busy = False
        app._refreshing = False
        for name in (
            "add",
            "scan",
            "refresh",
            "start",
            "stop",
            "auto",
            "remove",
            "rename",
        ):
            setattr(app, f"_btn_{name}", MagicMock())
        app._selected_name = lambda: "primary"
        app._status_by_name = {
            "primary": InstanceStatus(
                "primary",
                True,
                True,
                True,
                None,
                "wt0",
                "u",
                "a",
                managed_externally=True,
            )
        }
        app._update_buttons()
        app._btn_remove.configure.assert_called_with(state="disabled")
        app._status_by_name["primary"] = InstanceStatus(
            "primary", True, True, True, None, "wt1", "u", "a"
        )
        app._update_buttons()
        app._btn_remove.configure.assert_called_with(state="normal")


class TestCliCommands:
    def test_new_commands_registered(self) -> None:
        from twinbird.cli import app

        names = {cmd.name or cmd.callback.__name__ for cmd in app.registered_commands}
        for expected in (
            "up",
            "down",
            "status",
            "list",
            "remove",
            "autostart",
            "import",
            "gui",
        ):
            assert expected in names
