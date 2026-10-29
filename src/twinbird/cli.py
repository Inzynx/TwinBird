from importlib.metadata import version
from typing import Annotated

import typer

from twinbird import instance as inst


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"twinbird {version('twinbird')}")
        raise typer.Exit


app = typer.Typer(
    name="twinbird",
    help="Manage multiple NetBird instances with isolated configs and interfaces.",
)


@app.callback()
def main(
    _version: Annotated[
        bool,
        typer.Option(
            "-v",
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
) -> None:
    pass


@app.command()
def up(
    name: str = typer.Argument(help="Instance name"),
    management_url: str = typer.Option(
        ...,
        "--management-url",
        envvar="TWINBIRD_MANAGEMENT_URL",
        help="NetBird management URL",
    ),
    setup_key: Annotated[
        str | None,
        typer.Option(
            "--setup-key",
            envvar="TWINBIRD_SETUP_KEY",
            help="NetBird setup key (omit for OAuth login)",
        ),
    ] = None,
    interface_name: Annotated[
        str | None,
        typer.Option("--interface-name", help="Override WireGuard interface name"),
    ] = None,
    daemon_addr: Annotated[
        str | None,
        typer.Option("--daemon-addr", help="Override daemon address"),
    ] = None,
) -> None:
    """Start a named NetBird instance."""
    inst.up(
        name=name,
        management_url=management_url,
        setup_key=setup_key,
        interface_name=interface_name,
        daemon_addr=daemon_addr,
    )


@app.command()
def down(
    name: str = typer.Argument(help="Instance name"),
) -> None:
    """Stop a named NetBird instance."""
    inst.down(name)


@app.command()
def status(
    name: Annotated[
        str | None, typer.Argument(help="Instance name (omit for all)")
    ] = None,
) -> None:
    """Show status of one or all instances."""
    inst.status(name)


@app.command(name="list")
def list_cmd() -> None:
    """List all known instances and their state."""
    inst.list_all()


@app.command()
def remove(
    name: str = typer.Argument(help="Instance name"),
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Do not prompt for confirmation."),
    ] = False,
) -> None:
    """Stop an instance and delete all of its local state."""
    from twinbird import manager
    from twinbird.manager import ManagerError

    if not yes:
        typer.confirm(f"Remove instance '{name}' and delete its config?", abort=True)
    try:
        manager.remove(name, emit=typer.echo)
    except ManagerError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command()
def autostart(
    name: str = typer.Argument(help="Instance name"),
    enable: Annotated[
        bool | None,
        typer.Option(
            "--enable/--disable",
            help="Enable or disable start-on-boot for this instance.",
        ),
    ] = None,
) -> None:
    """Enable or disable start-on-boot for an instance."""
    from twinbird import manager
    from twinbird.manager import ManagerError

    if enable is None:
        typer.echo("Specify --enable or --disable.", err=True)
        raise typer.Exit(1)
    try:
        manager.set_autostart(name, enable, emit=typer.echo)
    except ManagerError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command(name="import")
def import_cmd(
    name: str = typer.Argument("primary", help="Name for the adopted instance"),
) -> None:
    """Detect an existing NetBird install and integrate it (read-only)."""
    from twinbird import manager
    from twinbird.manager import ManagerError

    try:
        manager.import_primary(name, emit=typer.echo)
    except ManagerError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command()
def gui() -> None:
    """Open the TwinBird desktop manager (system-tray icon + window)."""
    from twinbird.gui import run_gui

    run_gui()


if __name__ == "__main__":
    app()
