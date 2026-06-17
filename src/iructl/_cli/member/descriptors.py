"""Per-member-type CLI metadata for the generic command factory."""

from collections.abc import Callable
from dataclasses import dataclass

import typer

from iructl.repository import MemberBase, RepositoryDirectory

from .factory import default_delete, default_list, default_pull, default_push, default_sync

# Registers one lifecycle verb onto a member's Typer app; overrides the default builder.
type CommandBuilder = Callable[[typer.Typer, "MemberCliDescriptor"], None]


@dataclass(frozen=True)
class MemberCliDescriptor:
    """The per-type CLI presentation metadata the command factory needs."""

    member_type: type[MemberBase]  # concrete member class, e.g. CustomScript
    noun_singular: str  # lowercase singular noun for help/messages, e.g. "script"
    noun_plural: str  # lowercase plural noun for help/messages, e.g. "scripts"
    group_help: str  # help text shown for the member's command group

    # Per-verb builders default to the shared ones; a member type overrides those that diverge.
    push_command: CommandBuilder = default_push
    pull_command: CommandBuilder = default_pull
    sync_command: CommandBuilder = default_sync
    delete_command: CommandBuilder = default_delete
    list_command: CommandBuilder = default_list

    @property
    def member_name(self) -> str:
        """Human-readable type name, e.g. "custom script"."""
        return self.member_type._config.member_name

    @property
    def repo_directory(self) -> RepositoryDirectory:
        """The repository subdirectory this member type lives in."""
        return RepositoryDirectory(self.member_type.directory_name)
