"""TwinBird desktop manager: a small tkinter window plus a system-tray icon.

The window and tray are thin views over :mod:`twinbird.manager`; all lifecycle
logic lives there. Long-running actions run on worker threads and report back to
the Tk main thread through a queue drained by a periodic poll, so the UI stays
responsive and never touches Tk from a background thread.

Dependencies:
- tkinter (standard library) for the window.
- pystray + Pillow (optional, install with ``pip install 'twinbird[gui]'``) for
  the tray icon. Without them, the window still works; only the tray is skipped.
"""

from __future__ import annotations

import base64
import contextlib
import os
import platform as host_platform
import queue
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from io import BytesIO
from typing import Any

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except Exception as exc:  # pragma: no cover - platform without Tk
    raise RuntimeError(
        "The TwinBird GUI needs tkinter, which is not available in this Python. "
        "Install the Tk bindings for your platform and try again."
    ) from exc

from twinbird import manager
from twinbird.manager import InstanceStatus, ManagerError
from twinbird.netbird import find_netbird_bin
from twinbird.platform import InvalidInstanceName, validate_instance_name

APP_TITLE = "TwinBird"
APP_SUBTITLE = "Run several NetBird accounts side by side"
# NetBird Cloud management endpoint (NOT the dashboard app.netbird.io).
DEFAULT_MANAGEMENT_URL = "https://api.netbird.io:443"


# --------------------------------------------------------------------------- #
# Branding assets
# --------------------------------------------------------------------------- #


def _logo_png_bytes() -> bytes | None:
    """Return the bundled logo PNG bytes, or None if unavailable."""
    try:
        from importlib.resources import files

        assets = files("twinbird.assets")
        try:
            return (assets / "logo-transparent.png").read_bytes()
        except FileNotFoundError:
            return (assets / "logo.png").read_bytes()
    except Exception:
        return None


def _ico_path() -> str | None:
    """Materialise the bundled multi-size .ico to a stable temp path (Windows)."""
    try:
        from importlib.resources import files

        assets = files("twinbird.assets")
        try:
            data = (assets / "logo-transparent.ico").read_bytes()
        except FileNotFoundError:
            data = (assets / "logo.ico").read_bytes()
    except Exception:
        return None
    path = os.path.join(tempfile.gettempdir(), "twinbird-icon.ico")
    try:
        with open(path, "wb") as fh:
            fh.write(data)
        return path
    except OSError:
        return None


def _tray_available() -> bool:
    if _is_wsl():
        # WSLg provides app windows, but no X11 system tray manager for pystray.
        return False
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
    except Exception:
        return False
    return True


def _is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    release = host_platform.release().lower()
    return "microsoft" in release or bool(os.environ.get("WSL_DISTRO_NAME"))


def _is_windows_admin() -> bool:
    """Return whether this process has an elevated Windows administrator token."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _make_tray_image() -> Any:
    """Build the tray icon image (the bundled logo, else a drawn fallback)."""
    from PIL import Image, ImageDraw

    data = _logo_png_bytes()
    if data:
        with contextlib.suppress(Exception):
            return Image.open(BytesIO(data)).convert("RGBA").resize((64, 64))

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 10, 40, 44), fill=(45, 140, 220, 255))
    draw.ellipse((24, 22, 58, 56), fill=(245, 150, 35, 255))
    return img


# --------------------------------------------------------------------------- #
# Add-instance dialog
# --------------------------------------------------------------------------- #

_HELP_NAME = (
    "A short label you choose, e.g. 'work' or 'personal'. "
    "Letters, digits, '-' and '_' only."
)
_HELP_URL = (
    "Your NetBird management server. NetBird Cloud: https://api.netbird.io:443 "
    "(this is the API endpoint, not the app.netbird.io dashboard). Self-hosted: "
    "your own server, e.g. https://netbird.example.com:443. If you already use "
    "NetBird on this PC, TwinBird fills this in for you automatically."
)
_HELP_KEY = (
    "Optional. Paste a setup key from the dashboard (Setup Keys) to connect "
    "without a browser. Leave blank to log in with your browser (OAuth / SSO)."
)
_HELP_IFACE = (
    "Optional. The WireGuard network adapter name. Leave blank to let TwinBird "
    "pick a free one automatically (recommended)."
)


class _AddDialog:
    """Modal form to create a new instance, with inline guidance per field."""

    def __init__(self, parent: tk.Tk) -> None:
        self.result: dict[str, str] | None = None
        self.top = tk.Toplevel(parent)
        self.top.title("Add instance")
        self.top.transient(parent)
        self.top.resizable(False, False)
        self.top.grab_set()
        self._build()
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.top.bind("<Escape>", lambda _e: self._cancel())
        self.top.wait_window(self.top)

    def _field(
        self, parent: tk.Widget, label: str, help_text: str, *, secret: bool
    ) -> ttk.Entry:
        block = ttk.Frame(parent)
        block.pack(fill="x", pady=(10, 0))
        ttk.Label(block, text=label, font=("", 9, "bold")).pack(anchor="w")
        entry = ttk.Entry(block, show="*" if secret else "")
        entry.pack(fill="x", pady=(3, 2))
        ttk.Label(
            block, text=help_text, foreground="#666", wraplength=460, justify="left"
        ).pack(anchor="w")
        return entry

    def _build(self) -> None:
        frame = ttk.Frame(self.top, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Connect a NetBird account", font=("", 12, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            frame,
            text="Fill in a name and your server URL. Everything else is optional.",
            foreground="#666",
            wraplength=460,
            justify="left",
        ).pack(anchor="w", pady=(2, 2))

        self.e_name = self._field(frame, "Name", _HELP_NAME, secret=False)
        self.e_url = self._field(frame, "Management URL", _HELP_URL, secret=False)

        # Prefill the server URL from the existing NetBird install if present,
        # otherwise the Cloud default. The primary config is only read, never
        # changed.
        detected = None
        with contextlib.suppress(Exception):
            from twinbird.detect import detected_management_url

            detected = detected_management_url()
        self.e_url.insert(0, detected or DEFAULT_MANAGEMENT_URL)
        if detected:
            ttk.Label(
                frame,
                text=f"Detected from your existing NetBird: {detected}",
                foreground="#1a7f37",
                wraplength=460,
                justify="left",
            ).pack(anchor="w", pady=(4, 0))
            try:
                configured_netbird = find_netbird_bin()
            except FileNotFoundError:
                configured_netbird = "netbird"
            if sys.platform == "win32" and configured_netbird == "netbird":
                ttk.Label(
                    frame,
                    text=(
                        "Windows limitation: NetBird 0.64.2 uses one fixed "
                        "Wintun adapter GUID. A second simultaneous interface "
                        "cannot be created by the stock client, even with a "
                        "different adapter name or administrator rights. Build "
                        "TwinBird's compatible client and set "
                        "TWINBIRD_NETBIRD_BIN to enable extra instances."
                    ),
                    foreground="#8a4b08",
                    wraplength=460,
                    justify="left",
                ).pack(anchor="w", pady=(6, 0))

        self.e_key = self._field(frame, "Setup key (optional)", _HELP_KEY, secret=True)
        self.e_iface = self._field(
            frame, "Interface (optional)", _HELP_IFACE, secret=False
        )

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(18, 0))
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(
            side="right", padx=(6, 0)
        )
        ttk.Button(buttons, text="Add & connect", command=self._ok).pack(side="right")

        self.top.update_idletasks()
        self.top.minsize(self.top.winfo_reqwidth(), self.top.winfo_reqheight())
        self.e_name.focus_set()

    def _ok(self) -> None:
        name = self.e_name.get().strip()
        try:
            validate_instance_name(name)
        except InvalidInstanceName as exc:
            messagebox.showerror("Invalid name", str(exc), parent=self.top)
            return
        if not self.e_url.get().strip():
            messagebox.showerror(
                "Missing URL", "A management URL is required.", parent=self.top
            )
            return
        self.result = {
            "name": name,
            "management_url": self.e_url.get().strip(),
            "setup_key": self.e_key.get().strip(),
            "interface_name": self.e_iface.get().strip(),
        }
        self.top.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.top.destroy()


# --------------------------------------------------------------------------- #
# Help / info tab content
# --------------------------------------------------------------------------- #

_HELP_SECTIONS: list[tuple[str, str]] = [
    (
        "What is TwinBird?",
        "TwinBird lets you run several NetBird accounts on this computer at the "
        "same time. Typical use: keep a personal and a "
        "work NetBird connected together. Each account is called an 'instance' "
        "and gets its own identity, network adapter and settings. TwinBird drives "
        "NetBird; the Windows multi-instance build is a separate patched client. "
        "This project is experimental.",
    ),
    (
        "Before you start",
        "1. Install NetBird (https://netbird.io) and make sure the 'netbird' "
        "command is available.\n"
        "2. Have your server URL ready. NetBird Cloud = https://api.netbird.io:443 "
        "(the API endpoint, not the app.netbird.io dashboard).\n"
        "3. Optionally create a setup key in the dashboard under 'Setup Keys' if "
        "you prefer connecting without a browser login.",
    ),
    (
        "Add your first instance",
        "Click 'Add instance', then fill in:\n"
        "- Name: a label you choose, e.g. 'work' or 'personal'.\n"
        "- Management URL: your NetBird server. Cloud = https://api.netbird.io:443; "
        "self-hosted = your server address. If NetBird is already set up on this "
        "PC, TwinBird fills this in for you.\n"
        "- Setup key (optional): paste a key to connect. GUI browser login "
        "without a key is experimental.\n"
        "- Interface (optional): leave blank; TwinBird assigns a free adapter.\n"
        "Press 'Add & connect'. The instance starts and appears in the list.",
    ),
    (
        "Using your existing NetBird",
        "If you already run NetBird on this PC, you do not need to change anything. "
        "TwinBird only reads your existing configuration (never edits it) to "
        "pre-fill the correct server URL, and it keeps new instances off your "
        "existing network adapter and WireGuard port so the current connection "
        "stays stable. Each instance is still a separate NetBird peer, so it needs "
        "its own login or setup key (a NetBird requirement, not a TwinBird one).",
    ),
    (
        "Start, stop and autostart",
        "Select an instance in the list to enable its buttons.\n"
        "- Start / Stop: connect or disconnect that instance (double-click a row "
        "to toggle).\n"
        "- Autostart: registers a startup entry; reconnect depends on platform "
        "permissions.\n"
        "- Remove: stop and delete a managed instance's local settings. "
        "The adopted primary cannot be removed. Delete any remote peer in the "
        "NetBird dashboard separately.",
    ),
    (
        "Adapters, ports and NetBird features",
        "TwinBird leaves NetBird in charge of DNS, SSH, the network monitor, MTU "
        "and routing; it does not disable any of them. The only things it sets per "
        "instance are a unique network adapter name and a unique WireGuard UDP "
        "port, because two interfaces cannot share one port. Your primary "
        "NetBird's adapter (usually wt0) and port (usually 51820) are left alone. "
        "On Windows, NetBird 0.64.2 also requests one fixed Wintun GUID, so a "
        "different adapter name cannot make a second interface work.",
    ),
    (
        "Running several accounts together",
        "Linux and macOS support is experimental and shares the host routing "
        "table and DNS setup. On Windows with the stock NetBird 0.64.2 "
        "client, a second simultaneous interface fails because each process "
        "requests the same Wintun GUID. The peer may be registered before that "
        "failure, but changing the adapter name or running as administrator does "
        "not fix it. Windows multi-instance support needs a NetBird build that "
        "assigns a unique Wintun GUID per instance. The build script creates "
        "a separate compatible client; it does not replace your installation.",
    ),
    (
        "Status snapshots",
        "Status is checked when the app opens, after an action, and when you "
        "click Refresh status. The bottom bar shows the last completed check. "
        "State describes the daemon; Management shows whether NetBird reported "
        "a Management connection. Unknown means the status was not available.",
    ),
    (
        "Security",
        "- Each instance is isolated: its own NetBird identity, keys and state in "
        "its own folder, separate from your primary NetBird.\n"
        "- Windows daemons use a loopback port; Linux and macOS use Unix sockets.\n"
        "- Setup keys are written to a temporary file and never placed on a "
        "command line; TwinBird does not store them.\n"
        "- Creating a network adapter needs administrator rights, so run TwinBird "
        "as administrator.\n"
        "See SECURITY.md in the project for the full model.",
    ),
    (
        "Command line",
        "Everything here is also available from the terminal:\n"
        "  twinbird up <name> --management-url <url> [--setup-key <key>]\n"
        "  twinbird down <name>\n"
        "  twinbird status | twinbird list\n"
        "  twinbird autostart <name> --enable | --disable\n"
        "  twinbird remove <name>",
    ),
]


# --------------------------------------------------------------------------- #
# Main application
# --------------------------------------------------------------------------- #


class TwinBirdApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1040x650")
        self.root.minsize(800, 520)
        self._apply_theme()

        self._events: queue.Queue = queue.Queue()
        self._busy = False
        self._refreshing = False
        self._last_checked_at: str | None = None
        self._cancel_event: threading.Event | None = None
        self._tray: Any = None
        self._status_by_name: dict[str, InstanceStatus] = {}
        self._image_refs: list[Any] = []  # keep Tk images alive

        self._apply_window_icon()
        self._build_ui()
        self.root.after(150, self._drain_events)
        self.refresh()

    # -- Theme / spacing --------------------------------------------------- #

    def _apply_theme(self) -> None:
        style = ttk.Style(self.root)
        with contextlib.suppress(Exception):
            style.configure("Treeview", rowheight=32, font=("", 9))
            style.configure("Treeview.Heading", font=("", 9, "bold"), padding=(8, 6))
            style.configure("TButton", padding=(10, 5))
            style.configure("Toolbutton", padding=(10, 5))

    # -- Branding ---------------------------------------------------------- #

    def _photo_image(self, subsample: int = 1) -> Any:
        data = _logo_png_bytes()
        if not data:
            return None
        try:
            img = tk.PhotoImage(data=base64.b64encode(data).decode("ascii"))
            if subsample > 1:
                img = img.subsample(subsample, subsample)
            self._image_refs.append(img)
            return img
        except Exception:
            return None

    def _apply_window_icon(self) -> None:
        if sys.platform == "win32":
            # Distinct AppUserModelID so Windows shows our icon in the taskbar
            # instead of the generic python.exe icon.
            with contextlib.suppress(Exception):
                import ctypes

                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "TwinBird.Manager"
                )
            ico = _ico_path()
            if ico:
                with contextlib.suppress(Exception):
                    self.root.iconbitmap(default=ico)
                    return
        icon = self._photo_image()
        if icon is not None:
            with contextlib.suppress(Exception):
                self.root.iconphoto(True, icon)

    # -- UI construction --------------------------------------------------- #

    def _build_ui(self) -> None:
        self._build_header()
        if sys.platform == "win32" and not _is_windows_admin():
            self._build_admin_notice()

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=(4, 0))
        self._tab_instances = ttk.Frame(notebook)
        self._tab_help = ttk.Frame(notebook)
        notebook.add(self._tab_instances, text="Instances")
        notebook.add(self._tab_help, text="Help")

        self._build_instances_tab(self._tab_instances)
        self._build_help_tab(self._tab_help)

        self._status_var = tk.StringVar(value="Ready.")
        status = ttk.Frame(self.root, relief="groove")
        status.pack(fill="x", side="bottom")
        ttk.Label(
            status, textvariable=self._status_var, anchor="w", padding=(8, 3)
        ).pack(side="left", fill="x", expand=True)
        self._checked_var = tk.StringVar(value="Status not checked yet")
        ttk.Label(
            status, textvariable=self._checked_var, anchor="e", padding=(8, 3)
        ).pack(side="right")

    def _build_admin_notice(self) -> None:
        notice = ttk.Frame(self.root, padding=(12, 8))
        notice.pack(fill="x", padx=10, pady=(4, 2))
        notice.columnconfigure(0, weight=1)
        ttk.Label(
            notice,
            text=(
                "Administrator rights are required to create NetBird network "
                "adapters. Connecting an instance may fail until TwinBird is "
                "restarted as administrator."
            ),
            foreground="#8a4b08",
            wraplength=700,
            justify="left",
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Button(
            notice,
            text="Restart as administrator",
            command=self._restart_as_admin,
        ).grid(row=0, column=1, sticky="e")

    def _restart_as_admin(self) -> None:
        """Relaunch this entry point through the Windows UAC consent prompt."""
        if sys.platform != "win32":
            return
        try:
            import ctypes

            entry_point = os.path.abspath(sys.argv[0])
            args = list(sys.argv[1:])
            if entry_point.lower().endswith(".exe"):
                executable = entry_point
                parameters = subprocess.list2cmdline(args)
            else:
                executable = sys.executable
                parameters = subprocess.list2cmdline([entry_point, *args])
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", executable, parameters, None, 1
            )
            if result <= 32:
                if result == 1223:  # ERROR_CANCELLED: the user declined the UAC prompt.
                    return
                raise OSError(f"Windows ShellExecuteW returned {result}.")
        except Exception as exc:
            messagebox.showerror(
                "Could not restart as administrator",
                "Windows could not restart TwinBird with administrator rights.\n\n"
                f"{exc}",
                parent=self.root,
            )
            return
        self.root.destroy()

    def _build_header(self) -> None:
        header = ttk.Frame(self.root, padding=(12, 10, 12, 2))
        header.pack(fill="x")
        logo = self._photo_image(subsample=4)  # ~64px from the 256px asset
        if logo is not None:
            ttk.Label(header, image=logo).pack(side="left", padx=(0, 10))
        text = ttk.Frame(header)
        text.pack(side="left", anchor="w")
        ttk.Label(text, text=APP_TITLE, font=("", 15, "bold")).pack(anchor="w")
        ttk.Label(text, text=APP_SUBTITLE, foreground="#666").pack(anchor="w")
        ttk.Label(header, text="EXPERIMENTAL", foreground="#8a4b08").pack(side="right")

    def _build_instances_tab(self, parent: ttk.Frame) -> None:
        root = ttk.Frame(parent, padding=12)
        root.pack(fill="both", expand=True)

        toolbar = ttk.Frame(root, padding=(0, 0, 0, 10))
        toolbar.pack(fill="x")

        self._btn_add = ttk.Button(toolbar, text="Add instance", command=self._on_add)
        self._btn_add.pack(side="left")
        self._btn_scan = ttk.Button(
            toolbar, text="Scan for existing", command=self._on_scan
        )
        self._btn_scan.pack(side="left", padx=(6, 0))
        self._btn_refresh = ttk.Button(
            toolbar, text="Refresh status", command=self.refresh
        )
        self._btn_refresh.pack(side="right")

        actions = ttk.Frame(root, padding=(0, 0, 0, 8))
        actions.pack(fill="x")
        self._btn_start = ttk.Button(actions, text="Start", command=self._on_start)
        self._btn_stop = ttk.Button(actions, text="Stop", command=self._on_stop)
        self._btn_auto = ttk.Button(
            actions, text="Enable autostart", command=self._on_toggle_autostart
        )
        self._btn_remove = ttk.Button(actions, text="Remove", command=self._on_remove)
        for b in (self._btn_start, self._btn_stop, self._btn_auto, self._btn_remove):
            b.pack(side="left", padx=(0, 6))
        self._btn_cancel = ttk.Button(
            actions,
            text="Cancel operation",
            command=self._cancel_current_action,
            state="disabled",
        )
        self._btn_cancel.pack(side="right", padx=(6, 0))

        ttk.Label(
            root,
            text=(
                "Status is checked on startup, after actions and when you refresh. "
                "Your existing NetBird is protected from removal."
            ),
            foreground="#666",
        ).pack(anchor="w", pady=(0, 6))

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)

        # Empty-state shown when there are no instances yet.
        self._empty = ttk.Frame(body, padding=24)
        ttk.Label(
            self._empty,
            text="No instances yet",
            font=("", 13, "bold"),
        ).pack(pady=(10, 2))
        ttk.Label(
            self._empty,
            text="Click 'Add instance' to connect a NetBird account, or "
            "'Scan for existing' to integrate the NetBird already on this PC.\n"
            "Not sure what to fill in? See the Help tab.",
            foreground="#666",
            justify="center",
        ).pack(pady=(0, 10))
        ttk.Button(self._empty, text="Add instance", command=self._on_add).pack()

        # Table of instances.
        self._table_frame = ttk.Frame(body)
        columns = ("state", "management", "interface", "ip", "peer", "autostart", "url")
        self.tree = ttk.Treeview(
            self._table_frame, columns=columns, show="tree headings", height=8
        )
        self.tree.heading("#0", text="Instance")
        self.tree.heading("state", text="State")
        self.tree.heading("management", text="Management")
        self.tree.heading("interface", text="Adapter")
        self.tree.heading("ip", text="NetBird IP")
        self.tree.heading("peer", text="Peer name")
        self.tree.heading("autostart", text="Autostart")
        self.tree.heading("url", text="Management URL")
        self.tree.column("#0", width=135, minwidth=110, stretch=False)
        self.tree.column(
            "state", width=145, minwidth=125, anchor="center", stretch=False
        )
        self.tree.column(
            "management", width=110, minwidth=100, anchor="center", stretch=False
        )
        self.tree.column(
            "interface", width=85, minwidth=75, anchor="center", stretch=False
        )
        self.tree.column("ip", width=135, minwidth=115, anchor="center", stretch=False)
        self.tree.column("peer", width=210, minwidth=150, stretch=True)
        self.tree.column(
            "autostart", width=100, minwidth=85, anchor="center", stretch=False
        )
        self.tree.column("url", width=245, minwidth=190, stretch=True)
        self.tree.tag_configure("running", foreground="#16713b")
        self.tree.tag_configure("stopped", foreground="#737b87")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(
            self._table_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        hscroll = ttk.Scrollbar(
            self._table_frame, orient="horizontal", command=self.tree.xview
        )
        self.tree.configure(xscrollcommand=hscroll.set)
        hscroll.pack(side="bottom", fill="x")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_buttons())
        self.tree.bind("<Double-1>", self._on_row_double_click)

        logframe = ttk.LabelFrame(root, text="Activity log", padding=(8, 6))
        logframe.pack(fill="x", pady=(12, 0))
        self.log = tk.Text(
            logframe,
            height=5,
            state="disabled",
            wrap="word",
            relief="flat",
            background="#f6f6f6",
        )
        self.log.pack(fill="x")

    def _build_help_tab(self, parent: ttk.Frame) -> None:
        container = ttk.Frame(parent, padding=4)
        container.pack(fill="both", expand=True)
        text = tk.Text(container, wrap="word", padx=10, pady=8, relief="flat")
        scroll = ttk.Scrollbar(container, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)

        text.tag_configure("h", font=("", 11, "bold"), spacing1=10, spacing3=4)
        text.tag_configure("b", spacing3=8, lmargin1=4, lmargin2=4)
        for title, body in _HELP_SECTIONS:
            text.insert("end", title + "\n", "h")
            text.insert("end", body + "\n", "b")
        text.configure(state="disabled")

    # -- Threading plumbing ------------------------------------------------ #

    def _post(self, fn: Any) -> None:
        self._events.put(fn)

    def _drain_events(self) -> None:
        try:
            while True:
                self._events.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(150, self._drain_events)

    def _set_status(self, message: str) -> None:
        self._post(lambda: self._status_var.set(message))

    def _log(self, message: str) -> None:
        def write() -> None:
            self.log.configure(state="normal")
            self.log.insert("end", message + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
            self._status_var.set(message)

        self._post(write)

    def _run_async(self, work: Any, *, cancellable: bool = False) -> None:
        if self._busy or self._refreshing:
            self._set_status("Busy; please wait for the current action to finish.")
            return
        self._busy = True
        cancel_event = threading.Event() if cancellable else None
        self._cancel_event = cancel_event
        self._set_all_buttons("disabled")
        if cancel_event is not None:
            self._btn_cancel.configure(state="normal")

        def runner() -> None:
            try:
                if cancel_event is not None:
                    work(cancel_event)
                else:
                    work()
            except ManagerError as exc:
                self._log(f"Error: {exc}")
            except Exception as exc:
                self._log(f"Unexpected error: {exc}")
            finally:

                def finish() -> None:
                    self._busy = False
                    self._cancel_event = None
                    self._btn_cancel.configure(state="disabled")
                    self.refresh()

                self._post(finish)

        threading.Thread(target=runner, daemon=True).start()

    def _set_all_buttons(self, state: str) -> None:
        for b in (
            self._btn_start,
            self._btn_stop,
            self._btn_auto,
            self._btn_add,
            self._btn_scan,
            self._btn_remove,
            self._btn_cancel,
            self._btn_refresh,
        ):
            b.configure(state=state)

    def _cancel_current_action(self) -> None:
        event = self._cancel_event
        if event is None or event.is_set():
            return
        event.set()
        self._btn_cancel.configure(state="disabled")
        self._log("Cancellation requested. Stopping the connection attempt...")

    # -- Data / refresh ---------------------------------------------------- #

    def _selected_name(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def refresh(self) -> None:
        if self._busy or self._refreshing:
            return
        self._refreshing = True
        self._set_all_buttons("disabled")
        self._checked_var.set("Checking status...")

        def worker() -> None:
            try:
                statuses = manager.list_statuses()
            except Exception as exc:
                message = str(exc)

                def failed() -> None:
                    self._refreshing = False
                    if self._last_checked_at:
                        self._checked_var.set(
                            f"Last checked: {self._last_checked_at} (refresh failed)"
                        )
                    else:
                        self._checked_var.set("Refresh failed; status not checked")
                    self._log(f"Could not refresh: {message}")
                    self._update_buttons()

                self._post(failed)
                return
            checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " (local)"

            def completed() -> None:
                self._refreshing = False
                self._apply_statuses(statuses)
                self._last_checked_at = checked_at
                self._checked_var.set(f"Last checked: {checked_at}")

            self._post(completed)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_statuses(self, statuses: list[InstanceStatus]) -> None:

        self._status_by_name = {st.name: st for st in statuses}
        selected = self._selected_name()
        self.tree.delete(*self.tree.get_children())
        for st in statuses:
            self.tree.insert(
                "",
                "end",
                iid=st.name,
                text=st.name,
                values=(
                    st.state_label,
                    (
                        "Connected"
                        if st.management_connected is True
                        else "Disconnected"
                        if st.management_connected is False
                        else "Unknown"
                    ),
                    st.interface_name or "",
                    st.netbird_ip or "—",
                    st.peer_name or "—",
                    st.autostart_label,
                    st.management_url or "",
                ),
                tags=("running" if st.up else "stopped",),
            )

        # Toggle empty-state vs table.
        if statuses:
            self._empty.pack_forget()
            self._table_frame.pack(fill="both", expand=True)
            if selected and self.tree.exists(selected):
                self.tree.selection_set(selected)
            elif self.tree.get_children():
                self.tree.selection_set(self.tree.get_children()[0])
        else:
            self._table_frame.pack_forget()
            self._empty.pack(fill="both", expand=True)

        self._update_buttons()
        self._rebuild_tray_menu(statuses)

    def _update_buttons(self) -> None:
        """Enable only the actions that make sense for the current selection."""
        if self._busy or self._refreshing:
            return
        self._btn_add.configure(state="normal")
        self._btn_scan.configure(state="normal")
        self._btn_refresh.configure(state="normal")
        name = self._selected_name()
        st = self._status_by_name.get(name) if name else None
        if st is None:
            for b in (
                self._btn_start,
                self._btn_stop,
                self._btn_auto,
                self._btn_remove,
            ):
                b.configure(state="disabled")
            return
        self._btn_start.configure(state="disabled" if st.up else "normal")
        self._btn_stop.configure(state="normal" if st.up else "disabled")
        self._btn_remove.configure(
            state="disabled" if st.managed_externally else "normal"
        )
        if st.managed_externally:
            # Autostart of the primary is owned by the NetBird service.
            self._btn_auto.configure(state="disabled", text="Autostart (service)")
        else:
            self._btn_auto.configure(
                state="normal",
                text="Disable autostart" if st.persistent else "Enable autostart",
            )

    # -- Button handlers --------------------------------------------------- #

    def _on_row_double_click(self, _event: Any) -> None:
        name = self._selected_name()
        if not name:
            return
        st = self._status_by_name.get(name)
        if st is None:
            return
        if st.up:
            self._on_stop()
        else:
            self._on_start()

    def _confirm_primary(self, verb: str, st: InstanceStatus) -> bool:
        """For an adopted primary, warn that it is the main NetBird connection."""
        if not st.managed_externally:
            return True
        return messagebox.askyesno(
            f"{verb} your main NetBird?",
            f"'{st.name}' is your existing NetBird connection, shared with the "
            f"NetBird app. {verb}ing it here affects that same connection.\n\n"
            f"{verb} it now?",
            parent=self.root,
        )

    def _on_start(self) -> None:
        name = self._selected_name()
        if not name:
            return
        st = self._status_by_name.get(name)
        url = st.management_url if st else None
        if not st or not url:
            self._log(f"No stored management URL for '{name}'.")
            return
        if not self._confirm_primary("Connect", st):
            return
        self._log(f"Starting '{name}'...")
        self._run_async(
            lambda cancel: manager.start(
                name,
                url,
                interactive=False,
                emit=self._log,
                cancel_event=cancel,
            ),
            cancellable=True,
        )

    def _on_stop(self) -> None:
        name = self._selected_name()
        if not name:
            return
        st = self._status_by_name.get(name)
        if st and not self._confirm_primary("Disconnect", st):
            return
        self._log(f"Stopping '{name}'...")
        self._run_async(lambda: manager.stop(name, emit=self._log))

    def _on_toggle_autostart(self) -> None:
        name = self._selected_name()
        if not name:
            return
        st = self._status_by_name.get(name)
        if st is None:
            return
        self._run_async(
            lambda: manager.set_autostart(name, not st.persistent, emit=self._log)
        )

    def _on_scan(self) -> None:
        self._log("Scanning for an existing NetBird install...")
        self._run_async(lambda: manager.import_primary(emit=self._log))

    def _on_add(self) -> None:
        dialog = _AddDialog(self.root)
        if not dialog.result:
            return
        data = dialog.result
        self._log(f"Adding '{data['name']}'...")
        self._run_async(
            lambda cancel: manager.start(
                name=data["name"],
                management_url=data["management_url"],
                setup_key=data["setup_key"] or None,
                interface_name=data["interface_name"] or None,
                interactive=False,
                emit=self._log,
                cancel_event=cancel,
            ),
            cancellable=True,
        )

    def _on_remove(self) -> None:
        name = self._selected_name()
        if not name:
            return
        st = self._status_by_name.get(name)
        if st and st.managed_externally:
            self._log(f"'{name}' is your existing NetBird and cannot be removed.")
            return
        if not messagebox.askyesno(
            "Remove instance",
            f"Stop '{name}', remove its autostart and delete its local settings?\n\n"
            "Its peer may still appear in the NetBird dashboard.",
            parent=self.root,
        ):
            return
        self._run_async(lambda: manager.remove(name, emit=self._log))

    # -- Tray -------------------------------------------------------------- #

    def _start_tray(self) -> None:
        if _is_wsl():
            self._log(
                "WSL has no system tray host; TwinBird is running in window-only mode."
            )
            return
        if not _tray_available():
            self._log(
                "System tray unavailable. Install 'twinbird[gui]' to enable its icon."
            )
            return
        import pystray

        self._pystray = pystray
        self._tray = pystray.Icon(
            "twinbird", _make_tray_image(), APP_TITLE, menu=self._build_tray_menu([])
        )
        threading.Thread(target=self._tray.run, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self._hide_window)

    def _build_tray_menu(self, statuses: list[InstanceStatus]) -> Any:
        import pystray

        items = [
            pystray.MenuItem("Open manager", self._show_window, default=True),
            pystray.Menu.SEPARATOR,
        ]
        for st in statuses:
            items.append(
                pystray.MenuItem(
                    f"{st.name} ({'on' if st.up else 'off'})",
                    self._make_tray_toggle(st.name),
                    checked=lambda item, n=st.name: self._is_up(n),
                )
            )
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Quit", self._quit))
        return pystray.Menu(*items)

    def _rebuild_tray_menu(self, statuses: list[InstanceStatus]) -> None:
        if self._tray is None:
            return
        with contextlib.suppress(Exception):
            self._tray.menu = self._build_tray_menu(statuses)
            self._tray.update_menu()

    def _is_up(self, name: str) -> bool:
        st = self._status_by_name.get(name)
        return bool(st and st.up)

    def _make_tray_toggle(self, name: str) -> Any:
        def toggle(_icon: Any, _item: Any) -> None:
            def do() -> None:
                st = self._status_by_name.get(name)
                if st is None:
                    return
                if st.up:
                    self._run_async(lambda: manager.stop(name, emit=self._log))
                elif st.management_url:
                    url = st.management_url
                    self._run_async(
                        lambda cancel: manager.start(
                            name,
                            url,
                            interactive=False,
                            emit=self._log,
                            cancel_event=cancel,
                        ),
                        cancellable=True,
                    )

            self._post(do)

        return toggle

    def _show_window(self, *_: Any) -> None:
        def show() -> None:
            self.root.deiconify()
            self.root.lift()

        self._post(show)

    def _hide_window(self) -> None:
        if self._tray is not None:
            self.root.withdraw()
        else:
            self._quit()

    def _quit(self, *_: Any) -> None:
        def do() -> None:
            if self._tray is not None:
                with contextlib.suppress(Exception):
                    self._tray.stop()
            self.root.quit()
            self.root.destroy()

        self._post(do)

    # -- Lifecycle --------------------------------------------------------- #

    def run(self) -> None:
        self._start_tray()
        self.root.mainloop()


def run_gui() -> None:
    if sys.platform == "win32":
        # Detach a GUI launched from an elevated PowerShell/cmd session so
        # closing that console does not send a close event to TwinBird.
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.windll.kernel32.FreeConsole()
    TwinBirdApp().run()
