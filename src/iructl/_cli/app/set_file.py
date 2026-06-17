"""The `app set-file` command: point an app at a different installer in the payload dir."""

import logging
from pathlib import Path
from typing import Annotated

import typer

from iructl._cli.common import OptionalRepoPathOption, resolve_repo
from iructl._cli.payloads import protect_payload_dir, resolve_payload_dir
from iructl._cli.utility import get_member
from iructl._console import OutputConsole, epilog_text
from iructl._utils import locate_repo_root
from iructl.repository import AppFile, CustomApp

from .installer import import_installer
from .options import PayloadDirOption

__all__ = ["app"]

console = OutputConsole(logging.getLogger(__name__))

app = typer.Typer(rich_markup_mode="rich")

AppArgument = Annotated[
    str,
    typer.Argument(metavar="APP", show_default=False, help="ID of or path to the app to update."),
]
FileOption = Annotated[
    str,
    typer.Option(
        "--file",
        help="Installer to point the app at: a path to import, or a file name already in the payload directory.",
        metavar="FILE",
    ),
]
CopyModeFlag = Annotated[
    bool,
    typer.Option("--copy/--move", show_default="copy", help="Copy or move an imported installer into the repo."),
]


@app.command(name="set-file", no_args_is_help=True, epilog=epilog_text)
def set_file(
    ctx: typer.Context,
    app_arg: AppArgument,
    file: FileOption,
    copy_mode: CopyModeFlag = True,
    payload_dir: PayloadDirOption = None,
    repo_str: OptionalRepoPathOption = None,
):
    """Point an app at a different installer, importing it into the payload dir when needed."""

    member = get_member(config=None, member_type=CustomApp, key=app_arg, repo=resolve_repo(ctx, repo_str), remote=False)

    repo_root = locate_repo_root(cd_path=member.info_path)
    payload_path = resolve_payload_dir(repo_root, payload_dir)

    source = Path(file).expanduser()
    in_payload = payload_path / source.name
    if source.is_file():
        name, sha = import_installer(source, payload_path, copy_mode=copy_mode)
    elif in_payload.is_file():
        # Already in the payload dir: rename it into its content-suffixed name in place.
        name, sha = import_installer(in_payload, payload_path, copy_mode=False)
    else:
        msg = f"The installer '{file}' was not found at that path or in the payload directory ({payload_path})."
        console.error(msg)
        raise typer.BadParameter(msg)

    if ctx.obj.git:
        protect_payload_dir(payload_path, repo_root)

    member.info.file = AppFile(name=name, sha256=sha)
    member.write(write_content=False)
    console.print_success(f"Updated {member.name} to use installer '{name}'.")
