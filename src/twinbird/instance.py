from __future__ import annotations

import typer

from twinbird import manager
from twinbird.manager import ManagerError


def _run(action) -> None:  # type: ignore[no-untyped-def]
    try:
        action()
    except ManagerError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def up(
    name: str,
    management_url: str,
    setup_key: str | None = None,
    interface_name: str | None = None,
    daemon_addr: str | None = None,
) -> None:
    _run(
        lambda: manager.start(
            name=name,
            management_url=management_url,
            setup_key=setup_key,
            interface_name=interface_name,
            daemon_addr=daemon_addr,
            interactive=True,
            emit=typer.echo,
        )
    )


def down(name: str) -> None:
    _run(lambda: manager.stop(name, emit=typer.echo))


def status(name: str | None = None) -> None:
    if name:
        _run(lambda: typer.echo(manager.status_text(name)))
        return

    statuses = manager.list_statuses()
    if not statuses:
        typer.echo("No instances found.")
        return
    for st in statuses:
        if st.up:
            typer.echo(manager.status_text(st.name))
        else:
            typer.echo(f"--- {st.name} (stopped) ---")


def list_all() -> None:
    statuses = manager.list_statuses()
    if not statuses:
        typer.echo("No instances found.")
        return
    for st in statuses:
        typer.echo(f"{st.name}: {st.state_label}")
