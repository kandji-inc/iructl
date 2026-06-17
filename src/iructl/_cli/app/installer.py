"""Custom-app installer mechanics: payload-dir prep, sha256 hashing, and the push dry-run preview."""

import logging
import shutil
from pathlib import Path

import typer

from iructl._cli.common import ActionType, PreparedAction
from iructl._cli.payloads import ensure_payloads_gitignore, resolve_payload_dir, tracked_payload_paths
from iructl._cli.utility import validate_repo_path
from iructl._console import OutputConsole
from iructl._constants import APP_NAME
from iructl._utils import content_suffixed_filename, sha256_file
from iructl.repository import CustomApp, Repository
from iructl.repository.custom_app import DownloadResult

console = OutputConsole(logging.getLogger(__name__))


def payload_context(
    repo_str: str, override: str | None, *, dry_run: bool, ensure_gitignore: bool, git_enabled: bool = True
) -> tuple[Path, set[Path]]:
    """Resolve the payload dir and (unless dry-run) collect tracked binaries to unstage.

    The payload dir and its .gitignore are created only when ensure_gitignore is set, which
    callers tie to actually downloading installers; push and metadata-only pulls leave the
    directory untouched.
    """
    repo_root = validate_repo_path(repo=repo_str)
    payload_dir = resolve_payload_dir(repo_root, override)
    unstage: set[Path] = set()
    if not dry_run and git_enabled:
        if ensure_gitignore:
            ensure_payloads_gitignore(payload_dir, repo_root)
        unstage = tracked_payload_paths(payload_dir, repo_root)
    return payload_dir, unstage


def import_installer(source: Path, payload_dir: Path, *, copy_mode: bool) -> tuple[str, str]:
    """Place source into the payload dir under its content-suffixed name; returns (name, sha256).

    An identical file already at that name is left in place; a name collision with differing bytes errors.
    """
    source_sha = sha256_file(source)
    name = content_suffixed_filename(source.name, source_sha)
    installer = payload_dir / name
    if installer.exists():
        if sha256_file(installer) != source_sha:
            msg = (
                f"A different installer named '{name}' already exists in the payload directory "
                f"({payload_dir}). Rename the new file or remove the existing one."
            )
            console.error(msg)
            raise typer.BadParameter(msg)
    elif source.resolve() != installer.resolve():
        payload_dir.mkdir(parents=True, exist_ok=True)
        (shutil.copy if copy_mode else shutil.move)(source, installer)
    return name, source_sha


def warn_out_of_sync_installers(local_repo: Repository[CustomApp], *, payload_dir: Path) -> None:
    """Warn when a payload-dir installer doesn't match an app's recorded reference.

    Flags a clean-named installer whose bytes don't match the recorded sha256 (an installer
    changed without `app set-file`). A file at its exact content-suffixed name is trusted without
    hashing; an absent installer or a stray content-suffixed file is ignored.
    """
    for member in local_repo.values():
        if (payload_dir / member.info.file.name).is_file():
            continue  # trusted: the installer sits at its content-suffixed name
        if member.plan_download(payload_dir) is DownloadResult.MISMATCH_SKIPPED:
            console.print_warning(
                f"{member.name} ({member.id}): payload installer '{member.info.file.payload_name}' "
                f"doesn't match the recorded sha256. If you changed it locally, run "
                f"`{APP_NAME} app set-file {member.id} --file <installer>`.",
                stderr=False,
            )


def push_dry_run_preview(
    *,
    actions: list[PreparedAction[CustomApp]],
    local_repo: Repository[CustomApp],  # noqa: ARG001 (uniform dry-run preview signature)
    remote_repo: Repository[CustomApp],  # noqa: ARG001
) -> bool:
    """Print which installers a push would upload; return whether any upload is pending."""
    pending = False
    for action in actions:
        if action.action is ActionType.CREATE or (
            action.action is ActionType.UPDATE and action.member.would_upload(action.other)
        ):
            console.print(f"Would upload installer: [yellow]{action.member.name}[/] ([yellow]{action.member.id}[/])")
            pending = True
    return pending
