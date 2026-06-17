"""The `app download` command: fetch a single app's installer to the payload directory."""

import logging
from typing import Annotated

import typer

from iructl._cli.common import ApiTokenOption, IruTenantOption, OptionalRepoPathOption, resolve_repo
from iructl._cli.payloads import ensure_payloads_gitignore, resolve_payload_dir
from iructl._cli.utility import api_config_prompt, get_member, load_members_by_id, validate_repo_path
from iructl._console import OutputConsole, epilog_text
from iructl.exceptions import PayloadTransferError
from iructl.repository import CustomApp, RepositoryDirectory
from iructl.repository.custom_app import DownloadResult

from .options import PayloadDirOption

__all__ = ["app"]

console = OutputConsole(logging.getLogger(__name__))

app = typer.Typer(rich_markup_mode="rich")

AppArgument = Annotated[
    str,
    typer.Argument(metavar="APP", show_default=False, help="ID of or path to the app to download."),
]
ForceOption = Annotated[
    bool,
    typer.Option("--force", help="Overwrite a local installer that differs from the one in Iru."),
]


@app.command(name="download", no_args_is_help=True, epilog=epilog_text)
def download_app(
    ctx: typer.Context,
    member_arg: AppArgument,
    force: ForceOption = False,
    payload_dir: PayloadDirOption = None,
    repo_str: OptionalRepoPathOption = None,
    tenant_url: IruTenantOption = None,
    api_token: ApiTokenOption = None,
):
    """Download an app's installer binary from Iru into the payload directory."""

    repo = resolve_repo(ctx, repo_str)
    config = api_config_prompt(tenant_url, api_token)
    member = get_member(config=config, member_type=CustomApp, key=member_arg, repo=repo, remote=True)

    repo_root = validate_repo_path(repo=repo)
    payload_path = resolve_payload_dir(repo_root, payload_dir)
    if ctx.obj.git:
        ensure_payloads_gitignore(payload_path, repo_root)

    local = _local_member(member.id, repo)
    try:
        outcome = member.download_binary(
            payload_path, force=force, replaced_sha=local.info.file.sha256 if local is not None else None
        )
    except PayloadTransferError as error:
        console.print_error(f"Failed to download the installer for {member.name}: {error}")
        raise typer.Exit(code=1)

    if (
        local is not None
        and outcome in {DownloadResult.DOWNLOADED, DownloadResult.MIGRATED}
        and local.info.file.sha256 == member.info.file.sha256
    ):
        # Re-serialize so a pre-suffix stored file.name matches the on-disk installer.
        local.write()

    match outcome:
        case DownloadResult.DOWNLOADED:
            console.print_success(f"Downloaded {member.name} installer to {payload_path / member.info.file.name}")
        case DownloadResult.UP_TO_DATE:
            console.print(f"{member.name} installer is already present and up to date.")
        case DownloadResult.MIGRATED:
            console.print_success(
                f"Renamed the existing installer {member.info.file.payload_name} to {member.info.file.name}."
            )
        case DownloadResult.MISMATCH_SKIPPED:
            console.print_error(
                f"The local installer for {member.name} differs from Iru. Re-run with --force to overwrite it."
            )
            raise typer.Exit(code=1)


def _local_member(member_id: str, repo: str) -> CustomApp | None:
    """The local repo's member for this app, or None when it isn't tracked locally."""
    try:
        repo_path = validate_repo_path(repo=repo, subdir=RepositoryDirectory(CustomApp.directory_name))
        return next(
            load_members_by_id(
                repo_path=repo_path, member_type=CustomApp, member_ids=[member_id], raise_on_missing=False
            ),
            None,
        )
    except (typer.Exit, typer.BadParameter):
        return None
