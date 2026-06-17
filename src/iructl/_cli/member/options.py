"""Typer option definitions for the generic member commands."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

import typer

from iructl._cli.common import ForceMode

if TYPE_CHECKING:
    from .descriptors import MemberCliDescriptor

__all__ = [
    "DeleteLocalOnlyFlag",
    "DeleteRemoteOnlyFlag",
    "ListLocalOnlyFlag",
    "ListRemoteOnlyFlag",
    "NounAll",
    "NounDeleteForce",
    "NounId",
    "NounOption",
    "NounOptions",
    "NounPath",
    "NounPullClean",
    "NounPushClean",
    "PullForceOption",
    "PushForceOption",
    "SyncForceModeOption",
    "build_noun_options",
]


@dataclass(frozen=True)
class NounOption:
    """Marker placed in a command signature, standing in for a per-noun option.

    command_builder resolves each marker to the real Annotated option built by
    build_noun_options(desc) at registration, so the per-noun help text is generated then.
    """

    field: str  # the NounOptions attribute to substitute (e.g. "path", "push_clean")


# Markers for the per-noun options; command_builder swaps these for build_noun_options(desc).<field>.
NounPath = Annotated[list[str], NounOption("path")]
NounId = Annotated[list[UUID], NounOption("id")]
NounAll = Annotated[bool, NounOption("all")]
NounPullClean = Annotated[bool, NounOption("pull_clean")]
NounPushClean = Annotated[bool, NounOption("push_clean")]
NounDeleteForce = Annotated[bool, NounOption("delete_force")]


# --- Noun-neutral options (identical across every member type) ---
PullForceOption = Annotated[
    bool,
    typer.Option("--force", help="[red]Overwrite[/] local changes instead of reporting conflicts."),
]
PushForceOption = Annotated[
    bool,
    typer.Option("--force", help="[red]Overwrite[/] remote changes instead of reporting conflicts."),
]
SyncForceModeOption = Annotated[
    ForceMode,
    typer.Option(
        "--force-mode",
        "-m",
        help="Resolve conflicts using specified operation without prompting the user.",
    ),
]
DeleteLocalOnlyFlag = Annotated[
    bool,
    typer.Option("--local", "-l", show_default=False, help="Only delete local version."),
]
DeleteRemoteOnlyFlag = Annotated[
    bool,
    typer.Option("--remote", "-r", show_default=False, help="Only delete remote version."),
]
ListLocalOnlyFlag = Annotated[
    bool,
    typer.Option("--local", "-l", show_default=False, help="Only show local results."),
]
ListRemoteOnlyFlag = Annotated[
    bool,
    typer.Option("--remote", "-r", show_default=False, help="Only show remote results."),
]


@dataclass(frozen=True)
class NounOptions:
    """The per-noun ``Annotated`` option types for one member type."""

    path: object
    id: object
    all: object
    pull_clean: object
    push_clean: object
    delete_force: object


def build_noun_options(desc: "MemberCliDescriptor") -> NounOptions:
    """Build the option types whose help text carries the member noun."""

    noun, plural = desc.noun_singular, desc.noun_plural
    panel = f"{noun.capitalize()} Selection"
    return NounOptions(
        path=Annotated[
            list[str],
            typer.Option(
                "--path",
                show_default=False,
                rich_help_panel=panel,
                metavar="PATH",
                help=f"Include {noun}(s) at path.",
            ),
        ],
        id=Annotated[
            list[UUID],
            typer.Option("--id", show_default=False, rich_help_panel=panel, help=f"Include {noun} with ID."),
        ],
        all=Annotated[
            bool,
            typer.Option("--all", rich_help_panel=panel, help=f"Include all {plural}."),
        ],
        pull_clean=Annotated[
            bool,
            typer.Option("--clean", help=f"[red]Delete[/] local {plural} which are not present in Iru."),
        ],
        push_clean=Annotated[
            bool,
            typer.Option(
                "--clean",
                help=f"[red]Delete[/] remote {plural} which are not present in the local repository.",
            ),
        ],
        delete_force=Annotated[
            bool,
            typer.Option("--force", "-f", help=f"[red]Delete {plural} without prompting for confirmation.[/]"),
        ],
    )
