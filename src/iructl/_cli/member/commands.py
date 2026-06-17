"""The lifecycle command group: a git-availability callback and the registration entry point."""

import logging
from typing import TYPE_CHECKING

import typer

from iructl import _git
from iructl._console import OutputConsole

if TYPE_CHECKING:
    from .descriptors import MemberCliDescriptor

__all__ = ["register_lifecycle_commands"]

console = OutputConsole(logging.getLogger(__name__))


def _git_callback(ctx: typer.Context) -> None:
    """Ensure the git executable is available before any member command runs."""

    if not ctx.obj.git:
        return

    try:
        _git.locate_git()
    except FileNotFoundError as error:
        console.print_error("Unable to locate the `git` executable")
        console.print_error("Please make sure `git` is installed and available in your PATH.", style="none")
        raise typer.Exit(code=1) from error


def register_lifecycle_commands(app: typer.Typer, desc: "MemberCliDescriptor") -> typer.Typer:
    """Register the lifecycle commands (pull/push/sync/delete/list) onto app and return it."""

    app.callback(help=desc.group_help)(_git_callback)
    desc.pull_command(app, desc)
    desc.push_command(app, desc)
    desc.sync_command(app, desc)
    desc.delete_command(app, desc)
    desc.list_command(app, desc)
    return app
