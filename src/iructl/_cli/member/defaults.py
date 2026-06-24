"""Shared, member-agnostic bodies for the lifecycle commands."""

import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import typer

from iructl import _git
from iructl._cli.common import ActionType, ForceMode, OperationType, PreparedAction
from iructl._cli.utility import (
    InvalidRemoteMember,
    api_config_prompt,
    compute_exit_code,
    do_pulls,
    do_pushes,
    do_sync,
    filter_changes,
    filter_invalid_members,
    format_list_table,
    format_plain_text_list,
    get_local_members,
    get_remote_members,
    prepare_delete_actions,
    prepare_pull_actions,
    prepare_push_actions,
    prepare_sync_actions,
    record_invalid_members,
    reformat_members,
    save_report,
    show_blueprint_dry_run,
    show_blueprint_report,
    show_delete_report,
    show_pull_report,
    show_push_report,
    show_sync_report,
    validate_repo_path,
    verify_all_ids_found,
    warn_preview_off_if_declared,
)
from iructl._console import OutputConsole, OutputFormat
from iructl._constants import APP_NAME
from iructl._diff import ChangeType
from iructl.api import ApiConfig
from iructl.exceptions import GitRepositoryError, InvalidRepositoryError
from iructl.repository import InfoFormat, MemberBase, Repository
from iructl.repository.custom_app import DownloadResult

if TYPE_CHECKING:
    from .descriptors import MemberCliDescriptor

console = OutputConsole(logging.getLogger(__name__))


class GitCommitStage(StrEnum):
    """Where a wrapping commit is attempted, relative to the operation it guards."""

    BEFORE_PULL = "before pull"
    BEFORE_PUSH = "before push"
    BEFORE_SYNC = "before sync"
    BEFORE_DELETE = "before delete"


@contextmanager
def _abort_on_git_error(action: GitCommitStage) -> Generator[None]:
    """Turn a commit failure into a user-facing error and a Typer abort."""
    try:
        yield
    except InvalidRepositoryError as error:
        # Git is enabled but the target is not a git repository
        console.print_error(
            f"Cannot commit changes {action}: {error} "
            f"Initialize a git repository ('git init') or re-run with '--no-git' to skip git interactions."
        )
        raise typer.Abort
    except GitRepositoryError as error:
        console.print_error(f"Failed to commit changes to the local repository {action}: {error}")
        raise typer.Abort


# Runs inside a push/sync dry run; gets (actions, local_repo, remote_repo), prints the installer
# uploads that would happen, and returns whether any is pending.
type DryRunPreview = Callable[..., bool]

# Runs after the local repository is loaded for a push/sync; prints warnings for members whose
# local payload state is out of sync with their info file.
type PayloadCheck = Callable[[Repository], None]


def _preview_transfers(
    dry_run_preview: DryRunPreview | None, actions: list, local_repo: object, remote_repo: object
) -> bool:
    """Run the optional dry-run upload preview, returning whether any upload is pending."""
    if dry_run_preview is None:
        return False
    return dry_run_preview(actions=actions, local_repo=local_repo, remote_repo=remote_repo)


def _installer_mismatch_skip_line(action) -> str | None:
    """The dry-run 'would skip' line for an installer-mismatch TRANSFER, or None when not one."""
    if action.action is ActionType.TRANSFER and action.download is DownloadResult.MISMATCH_SKIPPED:
        member = f"[yellow]{action.member.name}[/] ([yellow]{action.member.id}[/])"
        return (
            f"Installer differs, would skip. Run `{APP_NAME} app download {action.member.id} --force` "
            f"to overwrite: {member}"
        )
    return None


def _installer_migrate_line(action) -> str | None:
    """The dry-run 'would rename' line for a legacy-installer TRANSFER, or None when not one."""
    if action.action is ActionType.TRANSFER and action.download is DownloadResult.MIGRATED:
        member = f"[yellow]{action.member.name}[/] ([yellow]{action.member.id}[/])"
        return f"Would rename installer to its content-addressed name: {member}"
    return None


def _print_dry_run_action(action, desc: "MemberCliDescriptor") -> None:
    """Print one prepared pull action in dry-run, wording an installer-only TRANSFER as a skip or rename."""
    special_line = _installer_mismatch_skip_line(action) or _installer_migrate_line(action)
    if special_line is not None:
        console.print(special_line)
    else:
        member = f"[yellow]{action.member.name}[/] ([yellow]{action.member.id}[/])"
        console.print(f"Would have {action.action.past_tense()} {desc.noun_singular}: {member}")
        if action.action is not ActionType.TRANSFER:
            if action.download is DownloadResult.DOWNLOADED:
                console.print(f"Would download installer: {member}")
            elif action.download is DownloadResult.MIGRATED:
                console.print(f"Would rename installer: {member}")


def _report_pending_reformats[MemberType: MemberBase](
    local_repo: Repository[MemberType],
    actions: list[PreparedAction[MemberType]],
    reformat: bool,
    info_format: InfoFormat | None,
    desc: "MemberCliDescriptor",
) -> list[MemberType]:
    """Report the dry-run reformats, excluding members about to be deleted."""
    if not reformat or info_format is None:
        return []
    delete_ids = {action.member.id for action in actions if action.action is ActionType.DELETE}
    pending = reformat_members(local_repo, info_format, dry_run=True, exclude_ids=delete_ids)
    for member in pending:
        console.print(
            f"Would have reformatted {desc.noun_singular}: [yellow]{member.name}[/] ([yellow]{member.id}[/]) "
            f"({member.info_path.name} -> info.{info_format})"
        )
    return pending


def _apply_reformats[MemberType: MemberBase](
    local_repo: Repository[MemberType], reformat: bool, info_format: InfoFormat | None
) -> None:
    if not reformat or info_format is None:
        return
    try:
        reformatted = reformat_members(local_repo, info_format)
    except Exception:
        console.print_warning(
            f"Reformat to {info_format} was interrupted; files converted before the failure remain "
            f"converted and uncommitted. Re-run with --reformat to finish the remaining files."
        )
        raise
    if reformatted:
        count = len(reformatted)
        console.print(f"Reformatted {count} info file{'s' if count > 1 else ''} to {info_format}.")


def run_pull(
    desc: "MemberCliDescriptor",
    *,
    repo_str: str,
    paths_str: list[str],
    member_ids: list[UUID],
    all_members: bool,
    force: bool,
    clean: bool,
    dry_run: bool,
    tenant_url: str | None,
    api_token: str | None,
    payload_dir: Path | None = None,
    download: bool = False,
    info_format: InfoFormat | None = None,
    reformat: bool = False,
    git_enabled: bool = True,
    unstage: set[Path] = set(),
) -> None:
    member_type = desc.member_type
    repo = validate_repo_path(repo=repo_str, subdir=desc.repo_directory)
    paths = [Path(path).expanduser().resolve() for path in paths_str]

    if clean and not all_members:
        msg = "The --clean option can only be used with --all"
        console.error(msg)
        raise typer.BadParameter(msg)

    if not (all_members or paths or member_ids):
        msg = f"No {desc.noun_plural} selected to pull. Use --all, --path, or --id to select {desc.noun_plural}."
        console.error(msg)
        raise typer.BadParameter(msg)

    if dry_run:
        console.print("Running in dry-run mode")

    config = api_config_prompt(tenant_url, api_token)

    member_ids_set = set(map(str, member_ids))

    local_repo = get_local_members(
        repo=repo,
        member_type=member_type,
        all_members=all_members,
        member_paths=paths,
        member_ids=member_ids_set,
        raise_on_missing_id=False,
        raise_on_missing_path=True,
    )

    member_ids_set |= set(local_repo.keys())

    remote_repo, invalid_members = get_remote_members(
        config=config,
        member_type=member_type,
        all_members=all_members,
        member_ids=member_ids_set,
        raise_on_missing=False,
    )

    local_repo, invalid_ids = filter_invalid_members(local_repo, invalid_members)

    verify_all_ids_found(
        member_ids=(member_id for member_id in map(str, member_ids) if member_id not in invalid_ids),
        local_repo=local_repo,
        remote_repo=remote_repo,
    )

    changes = filter_changes(local_repo=local_repo, remote_repo=remote_repo)

    actions = prepare_pull_actions(
        changes=changes, force_pull=force, allow_delete=clean, payload_dir=payload_dir, download=download
    )

    if dry_run:
        for action in actions:
            _print_dry_run_action(action, desc)
        pending_reformats = _report_pending_reformats(local_repo, actions, reformat, info_format, desc)
        if not actions and not pending_reformats:
            console.print(f"All specified {desc.noun_plural} are already up to date.")
        console.print("Dry run complete. No changes were made.")
        return

    num_actions = len([action for action in actions if action.action is not ActionType.SKIP])
    if num_actions > 0:
        console.print(f"Pulling {num_actions} change{'s' if num_actions > 1 else ''} from Iru...")

    with _abort_on_git_error(GitCommitStage.BEFORE_PULL):
        _git.commit_all_changes(
            cd_path=repo,
            message=f"Before pulling {desc.noun_plural} from Iru",
            scope=repo,
            unstage=unstage,
            enabled=git_enabled,
        )

    pull_results = do_pulls(
        local_repo=local_repo,
        actions=actions,
        payload_dir=payload_dir,
        force=force,
        info_format=info_format,
        announce_empty=not reformat,
    )
    record_invalid_members(pull_results, invalid_members)

    _apply_reformats(local_repo, reformat, info_format)

    commit_message = f"After pulling {desc.noun_plural} from Iru"
    try:
        _git.commit_all_changes(cd_path=repo, message=commit_message, scope=repo, enabled=git_enabled)
    except GitRepositoryError:
        console.print_error(
            f"Changes were pulled successfully but not committed to the local repository. "
            f"Please commit manually by running `git commit -am '{commit_message}'`."
        )

    save_report(results=pull_results)

    show_pull_report(pull_results=pull_results, changes=changes, force_pull=force, allow_delete=clean)
    console.print("Pull operation complete!")

    if compute_exit_code(pull_results):
        raise typer.Exit(code=1)


def run_push(
    desc: "MemberCliDescriptor",
    *,
    preview: bool,
    repo_str: str,
    paths_str: list[str],
    member_ids: list[UUID],
    all_members: bool,
    force: bool,
    clean: bool,
    dry_run: bool,
    tenant_url: str | None,
    api_token: str | None,
    git_enabled: bool = True,
    payload_dir: Path | None = None,
    dry_run_preview: DryRunPreview | None = None,
    payload_check: PayloadCheck | None = None,
    unstage: set[Path] = set(),
) -> None:
    member_type = desc.member_type
    repo = validate_repo_path(repo=repo_str, subdir=desc.repo_directory)
    paths = [Path(path).expanduser().resolve() for path in paths_str]

    if clean and not all_members:
        msg = "The --clean option can only be used with --all"
        console.error(msg)
        raise typer.BadParameter(msg)

    if not (all_members or paths or member_ids):
        msg = f"No {desc.noun_plural} selected to push. Use --all, --path, or --id to select {desc.noun_plural}."
        console.error(msg)
        raise typer.BadParameter(msg)

    if dry_run:
        console.print("Running in dry-run mode")

    config = api_config_prompt(tenant_url, api_token)

    member_ids_set = set(map(str, member_ids))

    local_repo = get_local_members(
        repo=repo,
        member_type=member_type,
        all_members=all_members,
        member_paths=paths,
        member_ids=member_ids_set,
        raise_on_missing_id=False,
        raise_on_missing_path=True,
    )

    member_ids_set |= set(local_repo.keys())

    # Warn if blueprints are declared but preview mode is off (before any push API call)
    if not preview:
        warn_preview_off_if_declared(local_repo)

    if payload_check is not None:
        payload_check(local_repo)

    remote_repo, invalid_members = get_remote_members(
        config=config,
        member_type=member_type,
        all_members=all_members,
        member_ids=member_ids_set,
        raise_on_missing=False,
    )

    local_repo, invalid_ids = filter_invalid_members(local_repo, invalid_members)

    verify_all_ids_found(
        member_ids=(member_id for member_id in map(str, member_ids) if member_id not in invalid_ids),
        local_repo=local_repo,
        remote_repo=remote_repo,
    )

    changes = filter_changes(local_repo=local_repo, remote_repo=remote_repo)

    actions = prepare_push_actions(changes=changes, force_push=force, allow_delete=clean)

    if dry_run:
        for action in actions:
            console.print(
                f"Would have {action.action.past_tense()} {desc.noun_singular}: "
                f"[yellow]{action.member.name}[/] ([yellow]{action.member.id}[/])"
            )
        blueprint_pending = show_blueprint_dry_run(local_repo, actions) if preview else False
        transfers_pending = _preview_transfers(dry_run_preview, actions, local_repo, remote_repo)
        if not actions and not blueprint_pending and not transfers_pending:
            console.print(f"All specified {desc.noun_plural} are already up to date.")
        console.print("Dry run complete. No changes were made.")
        return

    num_actions = len([action for action in actions if action.action is not ActionType.SKIP])
    if num_actions > 0:
        console.print(f"Pushing {num_actions} change{'s' if num_actions > 1 else ''} to Iru...")

    with _abort_on_git_error(GitCommitStage.BEFORE_PUSH):
        _git.commit_all_changes(
            cd_path=repo,
            message=f"Before pushing {desc.noun_plural} to Iru",
            scope=repo,
            unstage=unstage,
            enabled=git_enabled,
        )

    push_results = do_pushes(
        config=config, local_repo=local_repo, actions=actions, preview=preview, payload_dir=payload_dir
    )
    record_invalid_members(push_results, invalid_members)

    commit_message = f"After pushing {desc.noun_plural} to Iru"
    try:
        _git.commit_all_changes(cd_path=repo, message=commit_message, scope=repo, enabled=git_enabled)
    except GitRepositoryError:
        console.print_error(
            f"Changes were pushed successfully but not committed to the local repository. "
            f"Please commit manually by running `git commit -am '{commit_message}'`."
        )

    save_report(results=push_results, preview=preview)

    show_push_report(push_results=push_results, changes=changes, force_push=force, allow_delete=clean)
    if preview:
        show_blueprint_report(push_results)
    console.print("Push operation complete!")

    if compute_exit_code(push_results):
        raise typer.Exit(code=1)


def run_sync(
    desc: "MemberCliDescriptor",
    *,
    preview: bool,
    repo_str: str,
    paths_str: list[str],
    member_ids: list[UUID],
    all_members: bool,
    force_mode: ForceMode,
    dry_run: bool,
    tenant_url: str | None,
    api_token: str | None,
    payload_dir: Path | None = None,
    download: bool = False,
    info_format: InfoFormat | None = None,
    reformat: bool = False,
    git_enabled: bool = True,
    dry_run_preview: DryRunPreview | None = None,
    payload_check: PayloadCheck | None = None,
    unstage: set[Path] = set(),
) -> None:
    member_type = desc.member_type
    repo = validate_repo_path(repo=repo_str, subdir=desc.repo_directory)
    paths = [Path(path).expanduser().resolve() for path in paths_str]

    if not (all_members or paths or member_ids):
        msg = f"No {desc.noun_plural} selected to sync. Use --all, --path, or --id to select {desc.noun_plural}."
        console.error(msg)
        raise typer.BadParameter(msg)

    if dry_run:
        console.print("Running in dry-run mode")

    config = api_config_prompt(tenant_url, api_token)

    member_ids_set = set(map(str, member_ids))

    local_repo = get_local_members(
        repo=repo,
        member_type=member_type,
        all_members=all_members,
        member_paths=paths,
        member_ids=member_ids_set,
        raise_on_missing_id=False,
        raise_on_missing_path=True,
    )

    member_ids_set |= set(local_repo.keys())

    # Warn if blueprints are declared but preview mode is off (before any push API call)
    if not preview:
        warn_preview_off_if_declared(local_repo)

    if payload_check is not None:
        payload_check(local_repo)

    remote_repo, invalid_members = get_remote_members(
        config=config,
        member_type=member_type,
        all_members=all_members,
        member_ids=member_ids_set,
        raise_on_missing=False,
    )

    local_repo, invalid_ids = filter_invalid_members(local_repo, invalid_members)

    verify_all_ids_found(
        member_ids=(member_id for member_id in map(str, member_ids) if member_id not in invalid_ids),
        local_repo=local_repo,
        remote_repo=remote_repo,
    )

    changes = filter_changes(local_repo=local_repo, remote_repo=remote_repo)

    actions = prepare_sync_actions(changes=changes, force_mode=force_mode, payload_dir=payload_dir, download=download)

    if dry_run:
        for action in actions:
            special_line = _installer_mismatch_skip_line(action) or _installer_migrate_line(action)
            if special_line is not None:
                console.print(special_line)
                continue
            console.print(
                f"Would have {action.action.past_tense()} {desc.noun_singular} "
                f"{'locally' if action.operation is OperationType.PULL else 'in Iru'}: "
                f"[yellow]{action.member.name}[/] ([yellow]{action.member.id}[/])"
            )
            if action.operation is OperationType.PULL and action.action is not ActionType.TRANSFER:
                member = f"[yellow]{action.member.name}[/] ([yellow]{action.member.id}[/])"
                if action.download is DownloadResult.DOWNLOADED:
                    console.print(f"Would download installer: {member}")
                elif action.download is DownloadResult.MIGRATED:
                    console.print(f"Would rename installer: {member}")
        blueprint_pending = show_blueprint_dry_run(local_repo, actions) if preview else False
        transfers_pending = _preview_transfers(dry_run_preview, actions, local_repo, remote_repo)
        pending_reformats = _report_pending_reformats(local_repo, actions, reformat, info_format, desc)
        if not actions and not blueprint_pending and not transfers_pending and not pending_reformats:
            console.print(f"All specified {desc.noun_plural} are already up to date.")
        console.print("Dry run complete. No changes were made.")
        return

    num_actions = len([action for action in actions if action.action is not ActionType.SKIP])
    if num_actions > 0:
        console.print(f"Syncing {num_actions} change{'s' if num_actions > 1 else ''} with Iru...")

    with _abort_on_git_error(GitCommitStage.BEFORE_SYNC):
        _git.commit_all_changes(
            cd_path=repo,
            message=f"Before syncing {desc.noun_plural} with Iru",
            scope=repo,
            unstage=unstage,
            enabled=git_enabled,
        )

    sync_results = do_sync(
        config=config,
        local_repo=local_repo,
        actions=actions,
        preview=preview,
        payload_dir=payload_dir,
        force=force_mode is ForceMode.PULL,
        info_format=info_format,
    )
    record_invalid_members(sync_results, invalid_members)

    _apply_reformats(local_repo, reformat, info_format)

    commit_message = f"After syncing {desc.noun_plural} with Iru"
    try:
        _git.commit_all_changes(cd_path=repo, message=commit_message, scope=repo, enabled=git_enabled)
    except GitRepositoryError:
        console.print_error(
            f"Changes were synced successfully but not committed to the local repository. "
            f"Please commit manually by running `git commit -am '{commit_message}'`."
        )

    save_report(results=sync_results, preview=preview)

    show_sync_report(sync_results=sync_results, changes=changes, force_mode=force_mode)
    if preview:
        show_blueprint_report(sync_results)
    console.print("Sync operation complete!")

    if compute_exit_code(sync_results):
        raise typer.Exit(code=1)


def run_delete(
    desc: "MemberCliDescriptor",
    *,
    repo_str: str,
    paths_str: list[str],
    member_ids: list[UUID],
    all_members: bool,
    local_only: bool,
    remote_only: bool,
    force: bool,
    dry_run: bool,
    tenant_url: str | None,
    api_token: str | None,
    git_enabled: bool = True,
) -> None:
    member_type = desc.member_type
    repo = validate_repo_path(repo=repo_str, subdir=desc.repo_directory)
    paths = [Path(path).expanduser().resolve() for path in paths_str]

    if not (all_members or paths or member_ids):
        msg = f"No {desc.noun_plural} selected to delete. Use --all, --path, or --id to select {desc.noun_plural}."
        console.error(msg)
        raise typer.BadParameter(msg)

    if local_only and remote_only:
        msg = "The -r/--remote and -l/--local flags cannot be included at the same time."
        console.error(msg)
        raise typer.BadParameter(msg)

    if not repo.is_dir():
        msg = f"The path provided for --repo option does not exist. (got {repo.resolve()})"
        console.error(msg)
        raise typer.BadParameter(msg)

    if dry_run:
        console.print("Running in dry-run mode")

    member_ids_set = set(map(str, member_ids))

    local_repo = get_local_members(
        repo=repo,
        member_type=member_type,
        member_paths=paths,
        member_ids=member_ids_set,
        all_members=all_members,
        raise_on_missing_id=False,
        raise_on_missing_path=True,
    )

    member_ids_set |= set(local_repo.keys())

    invalid_members: list[InvalidRemoteMember] = []
    if local_only:
        config = ApiConfig(tenant_url="https://xxxxxxxx.api.iru.com", api_token="00000000-0000-0000-0000-000000000000")
        remote_repo = Repository[member_type]()
    else:
        config = api_config_prompt(tenant_url, api_token)
        remote_repo, invalid_members = get_remote_members(
            config=config,
            member_type=member_type,
            all_members=all_members,
            member_ids=member_ids_set,
            raise_on_missing=False,
        )

    local_repo, invalid_ids = filter_invalid_members(local_repo, invalid_members)
    # Deleting only one side of an item we cannot inspect would leave a surprising
    # asymmetric state, so delete excludes the ID set as well.
    member_ids_set -= invalid_ids

    if remote_only:
        member_ids_set = set(remote_repo.keys())
    else:
        member_ids_set |= set(remote_repo.keys())

    verify_all_ids_found(
        member_ids=(member_id for member_id in map(str, member_ids) if member_id not in invalid_ids),
        local_repo=local_repo,
        remote_repo=remote_repo,
        local_only=local_only,
        remote_only=remote_only,
    )

    actions = prepare_delete_actions(
        local_repo=local_repo,
        remote_repo=remote_repo,
        member_ids=member_ids_set,
        local_only=local_only,
        remote_only=remote_only,
    )

    if dry_run:
        for action in actions:
            console.print(
                f"Would have deleted {desc.noun_singular} "
                f"{'locally' if action.operation is OperationType.PULL else 'in Iru'}: "
                f"[yellow]{action.member.name} ({action.member.id})[/]"
            )
        if not actions:
            console.print("Nothing was selected for deletion.")
        console.print("Dry run complete. No changes were made.")
        return

    if not force:
        for action in actions:
            console.print(
                f"Will delete {desc.noun_singular} "
                f"{'locally' if action.operation is OperationType.PULL else 'in Iru'}: "
                f"[yellow]{action.member.name} ({action.member.id})[/]",
                style="bold red",
            )
        if actions:
            if typer.confirm("Do you want to continue?", abort=True):
                console.print("Confirmed.")

    if len(member_ids_set) > 0:
        console.print(f"Deleting {len(member_ids_set)} {desc.noun_singular}{'s' if len(member_ids_set) > 1 else ''}...")

    with _abort_on_git_error(GitCommitStage.BEFORE_DELETE):
        _git.commit_all_changes(
            cd_path=repo,
            message=f"Before deleting {desc.noun_plural} from Iru",
            scope=repo,
            enabled=git_enabled,
        )

    results = do_sync(config=config, local_repo=local_repo, actions=actions, description=f"Deleting {desc.noun_plural}")
    record_invalid_members(results, invalid_members)

    commit_message = f"After deleting {desc.noun_plural} from Iru"
    try:
        _git.commit_all_changes(cd_path=repo, message=commit_message, scope=repo, enabled=git_enabled)
    except GitRepositoryError:
        console.print_error(
            f"Changes were deleted successfully but not committed to the local repository. "
            f"Please commit manually by running `git commit -am '{commit_message}'`."
        )

    save_report(results=results)

    if actions:
        console.print("Delete operation complete!")
    show_delete_report(sync_results=results)


def run_list(
    desc: "MemberCliDescriptor",
    *,
    local_only: bool,
    remote_only: bool,
    include: list[ChangeType],
    exclude: list[ChangeType],
    format: OutputFormat,
    output: str,
    repo_str: str,
    tenant_url: str | None,
    api_token: str | None,
) -> None:
    member_type = desc.member_type
    repo = validate_repo_path(repo=repo_str, subdir=desc.repo_directory)

    if local_only and remote_only:
        msg = "The -r/--remote and -l/--local flags cannot be included at the same time."
        console.error(msg)
        raise typer.BadParameter(msg)

    if not repo.is_dir():
        msg = f"The path provided for --repo option does not exist. (got {repo.resolve()})"
        console.error(msg)
        raise typer.BadParameter(msg)

    invalid_members: list[InvalidRemoteMember] = []
    if local_only:
        remote_repo = Repository[member_type]()
    else:
        config = api_config_prompt(tenant_url, api_token)
        remote_repo, invalid_members = get_remote_members(config=config, member_type=member_type, all_members=True)

    if remote_only:
        local_repo = Repository[member_type]()
    else:
        local_repo = get_local_members(repo=repo, member_type=member_type, all_members=True)

    local_repo, _ = filter_invalid_members(local_repo, invalid_members)

    changes = filter_changes(local_repo=local_repo, remote_repo=remote_repo)

    include_set = set(include) if include else set(ChangeType)
    include_set -= set(exclude)
    changes = {change_type: change_list for change_type, change_list in changes.items() if change_type in include_set}

    if output == "-" and format is OutputFormat.TABLE:
        console.print(format_list_table(changes=changes, local_only=local_only, remote_only=remote_only))
    else:
        plain_output = format_plain_text_list(
            changes=changes, format=format, local_only=local_only, remote_only=remote_only
        )
        if output == "-":
            console.print_syntax(plain_output, syntax=format.to_syntax())
        else:
            output_path = Path(output).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8") as output_file:
                output_file.write(plain_output)
