"""The command_builder decorator and the shared default lifecycle commands."""

import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, get_args, get_origin

import typer

from iructl._cli.common import (
    ApiTokenOption,
    DryRunOption,
    ExcludeOption,
    ForceMode,
    FormatOption,
    IncludeOption,
    InfoFormatOption,
    IruTenantOption,
    OptionalRepoPathOption,
    OutputOption,
    ReformatOption,
    option_was_set,
    resolve_repo,
    validate_reformat,
)
from iructl._console import OutputFormat, epilog_text
from iructl._constants import IS_KST
from iructl.repository import InfoFormat

from .defaults import run_delete, run_list, run_pull, run_push, run_sync
from .options import (
    DeleteLocalOnlyFlag,
    DeleteRemoteOnlyFlag,
    ListLocalOnlyFlag,
    ListRemoteOnlyFlag,
    NounAll,
    NounDeleteForce,
    NounId,
    NounOption,
    NounPath,
    NounPullClean,
    NounPushClean,
    PullForceOption,
    PushForceOption,
    SyncForceModeOption,
    build_noun_options,
)

if TYPE_CHECKING:
    from .descriptors import CommandBuilder, MemberCliDescriptor

__all__ = [
    "command_builder",
    "default_delete",
    "default_list",
    "default_pull",
    "default_push",
    "default_sync",
]


def _resolve(annotation: object, opts: object) -> object:
    """Swap a NounOption marker for the real per-noun annotation; pass other annotations through."""
    if get_origin(annotation) is Annotated:
        for meta in get_args(annotation)[1:]:
            if isinstance(meta, NounOption):
                return getattr(opts, meta.field)
    return annotation


def command_builder(name: str, *, help: "str | Callable[[MemberCliDescriptor], str]", no_args_is_help: bool = True):
    """Decorate a function ``fn(desc, <typer params>)`` into a CommandBuilder.

    The returned builder, called with ``(app, desc)``, registers ``fn`` as a Typer command on
    ``app`` with ``desc`` bound and hidden from the CLI signature. NounOption markers in fn's
    signature are resolved to the per-noun options from build_noun_options(desc), and the command
    help is taken from ``help`` (resolved with ``desc`` when it is a callable).
    """

    def decorator(fn: Callable) -> "CommandBuilder":
        def builder(app: typer.Typer, desc: "MemberCliDescriptor") -> None:
            opts = build_noun_options(desc)

            def command(*args, **kwargs):
                return fn(desc, *args, **kwargs)

            # Expose fn's signature without the leading `desc`, with markers resolved per-noun.
            params = [
                param.replace(annotation=_resolve(param.annotation, opts))
                for param in list(inspect.signature(fn).parameters.values())[1:]
            ]
            command.__signature__ = inspect.Signature(params)  # type: ignore[reportAttributeAccessIssue]
            command.__annotations__ = {param.name: param.annotation for param in params}
            command.__doc__ = help(desc) if callable(help) else help
            app.command(name=name, no_args_is_help=no_args_is_help, epilog=epilog_text)(command)

        return builder

    return decorator


# --- Help builders (a member-noun-aware help string per verb) ---
def _pull_help(desc: "MemberCliDescriptor") -> str:
    help_text = f"""
    Pull remote {desc.member_name} changes from Iru.

    {desc.noun_plural.capitalize()} to pull can be selected using any combination of the --path and
    --id options. Both --path and --id can be used multiple times as needed. If
    --path is a directory, all {desc.noun_plural} in the directory will be included as if each
    was passed individually using --path.

    If --all is used, all {desc.noun_plural} will be pulled overriding other options.

    If --force is used, local changes to the selected {desc.noun_plural} will be
    overwritten.

    If --clean is used, {desc.noun_plural} will be deleted from the local repository if they
    are not in Iru. --clean can only be used with --all.
    """
    if not IS_KST:
        help_text += f"""
    --info-format sets the info-file format for newly created {desc.noun_plural}. With
    --reformat, existing in-scope info files are also rewritten into that format.

    """
    return help_text


def _push_help(desc: "MemberCliDescriptor") -> str:
    return f"""
    Push local {desc.member_name} changes to Iru.

    {desc.noun_plural.capitalize()} to push can be selected using any combination of the --path and
    --id options. Both --path and --id can be used multiple times as needed. If
    --path is a directory, all {desc.noun_plural} in the directory will be included as if each
    was passed individually using --path.

    If --all is used, all {desc.noun_plural} will be pushed overriding other options.

    If --force is used, remote changes to the selected {desc.noun_plural} will be
    overwritten.

    If --clean is used, {desc.noun_plural} will be deleted from Iru if they are not in
    the local repository. --clean can only be used with --all.

    """


def _sync_help(desc: "MemberCliDescriptor") -> str:
    help_text = f"""Sync {desc.member_name}s with Iru.

    {desc.noun_plural.capitalize()} to sync can be selected using any combination of the `--path` and
    `--id` options. Both `--path` and `--id` can be used multiple times as
    needed.

    If `--path` is used, Iru {desc.noun_plural} matching the ID of {desc.noun_plural} at the
    provided path will be synced.

    If `--path` is a directory, all {desc.noun_plural} in the directory will be included
    as if each was passed individually using `--path`.

    If `--all` is used, all {desc.noun_plural} will be synced overriding other options.
    """
    if not IS_KST:
        help_text += f"""
    `--info-format` sets the info-file format for newly created {desc.noun_plural}. With
    `--reformat`, existing in-scope info files are also rewritten into that format.
    """
    return help_text


def _delete_help(desc: "MemberCliDescriptor") -> str:
    return f"[red]Delete[/] {desc.noun_plural} from your local repository or Iru."


def _list_help(desc: "MemberCliDescriptor") -> str:
    return f"List all {desc.member_name}s in the repository."


# --- Shared lifecycle command definitions (each becomes a CommandBuilder) ---
@command_builder("pull", help=_pull_help)
def default_pull(
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
):
    validate_reformat(ctx, reformat)
    run_pull(
        desc,
        repo_str=resolve_repo(ctx, repo_str),
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
        git_enabled=ctx.obj.git,
    )


@command_builder("push", help=_push_help)
def default_push(
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
):
    run_push(
        desc,
        preview=ctx.obj.preview,
        repo_str=resolve_repo(ctx, repo_str),
        paths_str=paths_str,
        member_ids=member_ids,
        all_members=all_members,
        force=force,
        clean=clean,
        dry_run=dry_run,
        tenant_url=tenant_url,
        api_token=api_token,
        git_enabled=ctx.obj.git,
    )


@command_builder("sync", help=_sync_help)
def default_sync(
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
):
    validate_reformat(ctx, reformat)
    run_sync(
        desc,
        preview=ctx.obj.preview,
        repo_str=resolve_repo(ctx, repo_str),
        paths_str=paths_str,
        member_ids=member_ids,
        all_members=all_members,
        force_mode=force_mode,
        dry_run=dry_run,
        tenant_url=tenant_url,
        api_token=api_token,
        info_format=format if option_was_set(ctx, "format") else None,
        reformat=reformat,
        git_enabled=ctx.obj.git,
    )


@command_builder("delete", help=_delete_help)
def default_delete(
    desc: "MemberCliDescriptor",
    ctx: typer.Context,
    paths_str: NounPath = [],
    member_ids: NounId = [],
    all_members: NounAll = False,
    local_only: DeleteLocalOnlyFlag = False,
    remote_only: DeleteRemoteOnlyFlag = False,
    force: NounDeleteForce = False,
    repo_str: OptionalRepoPathOption = None,
    dry_run: DryRunOption = False,
    tenant_url: IruTenantOption = None,
    api_token: ApiTokenOption = None,
):
    run_delete(
        desc,
        repo_str=resolve_repo(ctx, repo_str),
        paths_str=paths_str,
        member_ids=member_ids,
        all_members=all_members,
        local_only=local_only,
        remote_only=remote_only,
        force=force,
        dry_run=dry_run,
        tenant_url=tenant_url,
        api_token=api_token,
        git_enabled=ctx.obj.git,
    )


@command_builder("list", help=_list_help, no_args_is_help=False)
def default_list(
    desc: "MemberCliDescriptor",
    ctx: typer.Context,
    local_only: ListLocalOnlyFlag = False,
    remote_only: ListRemoteOnlyFlag = False,
    include: IncludeOption = [],
    exclude: ExcludeOption = [],
    format: FormatOption = OutputFormat.TABLE,
    output: OutputOption = "-",
    repo_str: OptionalRepoPathOption = None,
    tenant_url: IruTenantOption = None,
    api_token: ApiTokenOption = None,
):
    run_list(
        desc,
        local_only=local_only,
        remote_only=remote_only,
        include=include,
        exclude=exclude,
        format=format,
        output=output,
        repo_str=resolve_repo(ctx, repo_str),
        tenant_url=tenant_url,
        api_token=api_token,
    )
