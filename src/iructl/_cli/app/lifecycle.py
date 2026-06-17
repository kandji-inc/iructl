"""The custom-app lifecycle overrides: push/pull/sync wired for installer upload and download."""

from functools import partial
from typing import TYPE_CHECKING

import typer

from iructl._cli.common import (
    ApiTokenOption,
    DryRunOption,
    ForceMode,
    InfoFormatOption,
    IruTenantOption,
    OptionalRepoPathOption,
    ReformatOption,
    option_was_set,
    resolve_repo,
    validate_reformat,
)
from iructl._cli.member.defaults import run_pull, run_push, run_sync
from iructl._cli.member.factory import command_builder
from iructl._cli.member.options import (
    NounAll,
    NounId,
    NounPath,
    NounPullClean,
    NounPushClean,
    PullForceOption,
    PushForceOption,
    SyncForceModeOption,
)
from iructl.repository import InfoFormat

from .installer import payload_context, push_dry_run_preview, warn_out_of_sync_installers
from .options import DownloadFlag, PayloadDirOption

if TYPE_CHECKING:
    from pathlib import Path

    from iructl._cli.member.descriptors import MemberCliDescriptor


def _app_push_help(desc: "MemberCliDescriptor") -> str:
    return f"""
    Push local {desc.member_name} changes to Iru, uploading installers as needed.

    {desc.noun_plural.capitalize()} to push are selected with --path, --id, or --all. An
    installer is uploaded only when its sha256 differs from the one in Iru; a
    metadata-only change needs no binary on disk.

    The installer for each app is read from the payload directory (default
    <repo>/payloads), overridable with --payload-dir.
    """


@command_builder("push", help=_app_push_help)
def app_push(
    desc: "MemberCliDescriptor",
    ctx: typer.Context,
    repo_str: OptionalRepoPathOption = None,
    paths_str: NounPath = [],
    member_ids: NounId = [],
    all_members: NounAll = False,
    force: PushForceOption = False,
    clean: NounPushClean = False,
    dry_run: DryRunOption = False,
    tenant_url: IruTenantOption = None,
    api_token: ApiTokenOption = None,
    payload_dir: PayloadDirOption = None,
):
    repo = resolve_repo(ctx, repo_str)
    payload_path, unstage = payload_context(
        repo, payload_dir, dry_run=dry_run, ensure_gitignore=False, git_enabled=ctx.obj.git
    )
    run_push(
        desc,
        preview=ctx.obj.preview,
        repo_str=repo,
        paths_str=paths_str,
        member_ids=member_ids,
        all_members=all_members,
        force=force,
        clean=clean,
        dry_run=dry_run,
        tenant_url=tenant_url,
        api_token=api_token,
        payload_dir=payload_path,
        dry_run_preview=push_dry_run_preview,
        payload_check=partial(warn_out_of_sync_installers, payload_dir=payload_path),
        unstage=unstage,
        git_enabled=ctx.obj.git,
    )


def _app_pull_help(desc: "MemberCliDescriptor") -> str:
    return f"""
    Pull remote {desc.member_name} changes from Iru.

    {desc.noun_plural.capitalize()} to pull are selected with --path, --id, or --all.

    With --download, every app in scope also has its installer ensured in the
    payload directory (default <repo>/payloads, overridable with --payload-dir):
    missing installers are downloaded, and a local installer that differs from Iru is
    left in place unless --force is given (the same flag that overrides metadata conflicts).
    """


@command_builder("pull", help=_app_pull_help)
def app_pull(
    desc: "MemberCliDescriptor",
    ctx: typer.Context,
    repo_str: OptionalRepoPathOption = None,
    paths_str: NounPath = [],
    member_ids: NounId = [],
    all_members: NounAll = False,
    force: PullForceOption = False,
    clean: NounPullClean = False,
    dry_run: DryRunOption = False,
    tenant_url: IruTenantOption = None,
    api_token: ApiTokenOption = None,
    format: InfoFormatOption = InfoFormat.PLIST,
    reformat: ReformatOption = False,
    download: DownloadFlag = False,
    payload_dir: PayloadDirOption = None,
):
    validate_reformat(ctx, reformat)
    repo = resolve_repo(ctx, repo_str)
    unstage: set[Path] = set()
    payload_path: Path | None = None
    if download:
        payload_path, unstage = payload_context(
            repo, payload_dir, dry_run=dry_run, ensure_gitignore=True, git_enabled=ctx.obj.git
        )
    run_pull(
        desc,
        repo_str=repo,
        paths_str=paths_str,
        member_ids=member_ids,
        all_members=all_members,
        force=force,
        clean=clean,
        dry_run=dry_run,
        tenant_url=tenant_url,
        api_token=api_token,
        info_format=format if option_was_set(ctx, "format") else None,
        reformat=reformat,
        payload_dir=payload_path,
        download=download,
        unstage=unstage,
        git_enabled=ctx.obj.git,
    )


def _app_sync_help(desc: "MemberCliDescriptor") -> str:
    return f"""Sync {desc.member_name}s with Iru.

    {desc.noun_plural.capitalize()} to sync are selected with --path, --id, or --all. The push
    half uploads an installer only when its sha256 differs from Iru's.

    With --download, the pull half also ensures each in-scope app's installer is
    present in the payload directory (default <repo>/payloads, overridable with
    --payload-dir); --force-mode pull overwrites a differing local installer along with
    metadata conflicts.
    """


@command_builder("sync", help=_app_sync_help)
def app_sync(
    desc: "MemberCliDescriptor",
    ctx: typer.Context,
    repo_str: OptionalRepoPathOption = None,
    paths_str: NounPath = [],
    member_ids: NounId = [],
    all_members: NounAll = False,
    force_mode: SyncForceModeOption = ForceMode.SKIP,
    dry_run: DryRunOption = False,
    tenant_url: IruTenantOption = None,
    api_token: ApiTokenOption = None,
    format: InfoFormatOption = InfoFormat.PLIST,
    reformat: ReformatOption = False,
    download: DownloadFlag = False,
    payload_dir: PayloadDirOption = None,
):
    validate_reformat(ctx, reformat)
    repo = resolve_repo(ctx, repo_str)
    payload_path, unstage = payload_context(
        repo, payload_dir, dry_run=dry_run, ensure_gitignore=download, git_enabled=ctx.obj.git
    )
    run_sync(
        desc,
        preview=ctx.obj.preview,
        repo_str=repo,
        paths_str=paths_str,
        member_ids=member_ids,
        all_members=all_members,
        force_mode=force_mode,
        dry_run=dry_run,
        tenant_url=tenant_url,
        api_token=api_token,
        info_format=format if option_was_set(ctx, "format") else None,
        reformat=reformat,
        payload_dir=payload_path,
        download=download,
        dry_run_preview=push_dry_run_preview,
        payload_check=partial(warn_out_of_sync_installers, payload_dir=payload_path),
        unstage=unstage,
        git_enabled=ctx.obj.git,
    )
