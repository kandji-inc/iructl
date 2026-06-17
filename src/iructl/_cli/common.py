import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import uuid4

import typer
from rich.panel import Panel

from iructl._console import OutputConsole, OutputFormat, SyntaxType
from iructl._constants import GIT_ENV, INFO_FORMAT_ENV, IS_KST, OUTPUT_FORMAT_ENV, PREVIEW_ENV, TENANT_ENV, TOKEN_ENV
from iructl._diff import ChangeType
from iructl.exceptions import KstUnavailableOption
from iructl.repository import InfoFormat, MemberBase

if TYPE_CHECKING:
    from iructl.repository.custom_app import DownloadResult

console = OutputConsole(logging.getLogger(__name__))


def reject_under_kst(ctx: typer.Context, param: typer.CallbackParam, value):
    """Make an iructl-era option inert under kst."""
    if not IS_KST or param.name is None:
        return value
    if option_was_set(ctx, param.name, sources=(OptionSource.COMMANDLINE,)):
        # Name the exact flag the user typed (e.g. --no-git rather than --git).
        flag = param.secondary_opts[0] if value is False and param.secondary_opts else param.opts[0]
        raise KstUnavailableOption(flag)
    if option_was_set(ctx, param.name, sources=(OptionSource.ENVIRONMENT,)):
        # Ignore the env var, resetting the source so option_was_set() gates also see it as unset.
        if (source := ctx.get_parameter_source(param.name)) is not None:
            ctx.set_parameter_source(param.name, type(source).DEFAULT)
        return param.default
    return value


# --- Shared Options ---
# General Options Panel
RepoPathOption = Annotated[
    str,
    typer.Option(
        "--repo",
        help="Path to the local repository.",
        metavar="DIRECTORY",
        show_default=False,
        hidden=IS_KST,
        callback=reject_under_kst,
    ),
]
OptionalRepoPathOption = Annotated[
    str | None,
    typer.Option(
        "--repo",
        help=(
            "Path to the local repository."
            if IS_KST
            else "(deprecated - use the top-level --repo) Path to the local repository."
        ),
        metavar="DIRECTORY",
        show_default=False,
        hidden=not IS_KST,
    ),
]
FormatOption = Annotated[
    OutputFormat,
    typer.Option(
        "--format",
        "-f",
        envvar=OUTPUT_FORMAT_ENV,
        help="Format to use for output.",
    ),
]
InfoFormatOption = Annotated[
    InfoFormat,
    typer.Option(
        "--info-format",
        envvar=INFO_FORMAT_ENV,
        help="Info-file format for newly created resources.",
        hidden=IS_KST,
        callback=reject_under_kst,
    ),
]
DeprecatedInfoFormatOption = Annotated[
    InfoFormat | None,
    typer.Option(
        "--format",
        "-f",
        hidden=not IS_KST,
        show_default=False,
        help=(
            "Info-file format for newly created resources."
            if IS_KST
            else "(deprecated - use --info-format) Info-file format for newly created resources."
        ),
    ),
]
OutputOption = Annotated[
    str,
    typer.Option(
        "--output",
        "-o",
        allow_dash=True,
        show_default="stdout",
        help="Output file",
    ),
]
ReformatOption = Annotated[
    bool,
    typer.Option(
        "--reformat",
        help="Rewrite existing info files into the --info-format format.",
        show_default=False,
        hidden=IS_KST,
        callback=reject_under_kst,
    ),
]
DryRunOption = Annotated[
    bool,
    typer.Option(
        "--dry-run",
        "-n",
        help="Perform a dry run without making changes.",
        show_default=False,
    ),
]
ForceOption = Annotated[
    bool,
    typer.Option(
        "--force",
        help="Overwrite instead of reporting conflicts.",
    ),
]
PreviewOption = Annotated[
    bool,
    typer.Option(
        "--preview",
        envvar=PREVIEW_ENV,
        help="Enable experimental preview features.",
        show_default=False,
        hidden=IS_KST,
        callback=reject_under_kst,
    ),
]
GitFlag = Annotated[
    bool,
    typer.Option(
        "--git/--no-git",
        envvar=GIT_ENV,
        help="Enable/disable git interactions.",
        hidden=IS_KST,
        callback=reject_under_kst,
    ),
]

# Iru Info Panel
IruTenantOption = Annotated[
    str | None,
    typer.Option(
        "--tenant-url",
        "-u",
        show_default=False,
        envvar=TENANT_ENV,
        rich_help_panel="Iru Info",
        help="Iru tenant URL",
    ),
]
ApiTokenOption = Annotated[
    str | None,
    typer.Option(
        "--api-token",
        "-t",
        show_default=False,
        envvar=TOKEN_ENV,
        rich_help_panel="Iru Info",
        help="Iru API token",
    ),
]

# Filters Panel
IncludeOption = Annotated[
    list[ChangeType],
    typer.Option(
        "--include",
        "-i",
        show_default=False,
        rich_help_panel="Filters",
        help="Include a specific change type in the output.",
    ),
]
ExcludeOption = Annotated[
    list[ChangeType],
    typer.Option(
        "--exclude",
        "-e",
        show_default=False,
        rich_help_panel="Filters",
        help="Exclude a specific change type from the output.",
    ),
]


def _deprecation_panel(body: str) -> Panel:
    """Build the standard "Deprecated option" warning panel."""
    return Panel(body, title="Deprecated option", title_align="left", border_style="warning", expand=False)


def resolve_info_format(info_format: InfoFormat, deprecated_format: InfoFormat | None) -> InfoFormat:
    """Return the effective info-file format, warning when the deprecated --format is used."""
    if deprecated_format is None:
        return info_format
    if not IS_KST:
        body = "`--format` is deprecated for the info-file format.\nUse `--info-format` instead."
        console.print_warning(_deprecation_panel(body))
    return deprecated_format


# --- Shared Data Objects ---
@dataclass
class GlobalState:
    """Root-level CLI state shared with subcommands via the Typer context (``ctx.obj``)."""

    preview: bool = False
    repo: str = "."
    git: bool = True


def resolve_repo(ctx: typer.Context, repo_str: str | None) -> str:
    """Return the effective repo path, warning when the deprecated subcommand --repo is used."""
    if repo_str is None:
        return ctx.obj.repo
    if not IS_KST:
        body = "`--repo` on subcommands is deprecated.\nUse the top-level `iructl --repo` instead."
        console.print_warning(_deprecation_panel(body))
    return repo_str


class OptionSource(StrEnum):
    """Non-default Click parameter sources, by ``ParameterSource`` name."""

    COMMANDLINE = "COMMANDLINE"
    ENVIRONMENT = "ENVIRONMENT"
    DEFAULT_MAP = "DEFAULT_MAP"


def option_was_set(ctx: typer.Context, name: str, sources: Collection[OptionSource] = tuple(OptionSource)) -> bool:
    """Whether a parameter was set via one of the given sources rather than its built-in default.

    Match the source by name, not identity -- Typer's vendored Click fork means an
    imported ``ParameterSource`` enum never compares equal.
    """
    source = ctx.get_parameter_source(name)
    return source is not None and source.name in sources


def validate_reformat(ctx: typer.Context, reformat: bool) -> None:
    """Reject --reformat unless --info-format was passed on the command line."""
    if reformat and not option_was_set(ctx, "format", sources=(OptionSource.COMMANDLINE,)):
        msg = "--reformat requires --info-format to be passed on the command line"
        console.error(msg)
        raise typer.BadParameter(msg)


class ForceMode(StrEnum):
    PUSH = "push"
    PULL = "pull"
    SKIP = "skip"


class ActionType(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    TRANSFER = "transfer"  # pull-only: download an installer for an app whose metadata is already in sync
    SKIP = "skip"
    INVALID = "invalid"  # a remote member whose payload could not be converted; recorded as a skipped failure

    def past_tense(self) -> str:
        """Get the past tense of the action type."""
        match self:
            case ActionType.CREATE:
                return "created"
            case ActionType.UPDATE:
                return "updated"
            case ActionType.DELETE:
                return "deleted"
            case ActionType.TRANSFER:
                return "downloaded"
            case ActionType.SKIP | ActionType.INVALID:
                return "skipped"


class OperationType(StrEnum):
    PUSH = "push"
    PULL = "pull"
    SKIP = "skip"


class ResultType(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    SKIPPED = "skipped"


class PayloadTransfer(StrEnum):
    """What happened to an item's installer binary."""

    NONE = "none"
    UPLOADED = "uploaded"
    DOWNLOADED = "downloaded"
    MIGRATED = "migrated"
    MISMATCH = "mismatch"
    FAILED = "failed"


@dataclass
class PreparedAction[MemberType: MemberBase]:
    """An api action to be performed."""

    action: ActionType
    operation: OperationType
    change: ChangeType
    member: MemberType
    other: MemberType | None = None  # the counterpart member (remote for a push, local for a pull)
    download: "DownloadResult | None" = None


@dataclass
class BlueprintActionOutcome:
    """The outcome of a single blueprint assign call for a (blueprint, node) pair."""

    blueprint_id: str  # Resolved UUID, or the raw declared value (name or UUID) when resolution failed.
    node_id: str | None
    status: Literal["assigned", "skipped", "failed"]
    status_code: int | None = None  # Populated only when status == "failed" with an HTTP response.
    error_message: str | None = (
        None  # Populated only when status == "failed"; response body for HTTP errors, exception type name otherwise.
    )
    blueprint_name: str | None = None  # Populated only when the assignment was declared by name.

    @property
    def blueprint_label(self) -> str:
        """Display label for the blueprint: the declared name when one was used, the UUID otherwise."""
        return self.blueprint_name or self.blueprint_id


def _serialize_blueprint_outcomes(outcomes: list[BlueprintActionOutcome]) -> list[dict]:
    """Serialize blueprint outcomes for the JSON report."""
    return [
        {
            "blueprint_id": outcome.blueprint_id,
            "blueprint_name": outcome.blueprint_name,
            "node_id": outcome.node_id,
            "status": outcome.status,
            "status_code": outcome.status_code,
            "error_message": outcome.error_message,
        }
        for outcome in outcomes
    ]


@dataclass
class ActionResponse[MemberType: MemberBase]:
    """The response from an api action."""

    id: str  # The original ID of the member since it may change on create actions
    action: ActionType
    operation: OperationType
    result: ResultType
    member: MemberType | None
    blueprint_outcomes: list[BlueprintActionOutcome] = field(default_factory=list)
    transfer: PayloadTransfer = PayloadTransfer.NONE
    reason: str | None = None

    @property
    def verb(self) -> str:
        """Display label for the action: a transfer-only pull reads as "Download"/"Rename" rather than "Transfer"."""
        if self.action is ActionType.TRANSFER:
            return "Rename" if self.transfer is PayloadTransfer.MIGRATED else "Download"
        return self.action.capitalize()


@dataclass
class SyncResults[MemberType: MemberBase]:
    """The results of a sync operation."""

    success: list[ActionResponse[MemberType]] = field(default_factory=list)
    partial: list[ActionResponse[MemberType]] = field(default_factory=list)
    failure: list[ActionResponse[MemberType]] = field(default_factory=list)
    skipped: list[ActionResponse[MemberType]] = field(default_factory=list)

    def format_summary(self) -> str:
        """Format the summary of the results."""

        success_count = len(self.success)
        partial_count = len(self.partial)
        failure_count = len(self.failure)
        skipped_count = len(self.skipped)

        summary = ""

        if success_count > 0:
            summary += f"\n\nSuccess ({success_count})"
            for success in self.success:
                summary += f"\n- {success.verb} {success.member.name + ' ' if success.member else ''}{success.id} {'in repository' if success.operation is OperationType.PULL else 'in Iru'}"

        if partial_count > 0:
            summary += f"\n\nPartial ({partial_count})"
            for partial in self.partial:
                summary += f"\n- {partial.verb} {partial.member.name + ' ' if partial.member else ''}{partial.id} {'in repository' if partial.operation is OperationType.PULL else 'in Iru'}"
                for outcome in partial.blueprint_outcomes:
                    if outcome.status == "failed":
                        node = f" (node {outcome.node_id})" if outcome.node_id else ""
                        summary += f"\n  - blueprint {outcome.blueprint_label}{node} failed ({outcome.status_code}): {outcome.error_message}"

        if failure_count > 0:
            summary += f"\n\nFailure ({failure_count})"
            for failure in self.failure:
                summary += f"\n- {failure.verb} {failure.member.name + ' ' if failure.member else ''}{failure.id} {'in repository' if failure.operation is OperationType.PULL else 'in Iru'}"

        if skipped_count > 0:
            summary += f"\n\nSkipped ({skipped_count})"
            for skipped in self.skipped:
                summary += f"\n- {skipped.verb} {skipped.member.name + ' ' if skipped.member else ''}{skipped.id} "

        return summary.lstrip()

    def format_report(self, preview: bool = False) -> dict:
        """Format the results for display.

        When preview is on, success and partial entries carry a ``blueprint_outcomes`` list
        (empty when the item recorded none). When off, the key is omitted entirely so the
        report stays backward-compatible with non-preview consumers.
        """

        return {
            "id": str(uuid4()),
            "timestamp": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "summary": self.format_summary(),
            "status": (
                "failure"
                if len(self.failure) != 0
                else "partial"
                if len(self.partial) != 0
                else "warning"
                if len(self.skipped) != 0
                else "success"
            ),
            "success": [
                {
                    "id": success.id,
                    "action": success.action,
                    "location": "local" if success.operation is OperationType.PULL else "remote",
                    "transfer": success.transfer,
                    "object": success.member.prepare_syntax_dict(syntax=SyntaxType.JSON) if success.member else None,
                    **(
                        {"blueprint_outcomes": _serialize_blueprint_outcomes(success.blueprint_outcomes)}
                        if preview
                        else {}
                    ),
                }
                for success in self.success
            ],
            "partial": [
                {
                    "id": partial.id,
                    "action": partial.action,
                    "location": "local" if partial.operation is OperationType.PULL else "remote",
                    "transfer": partial.transfer,
                    "object": partial.member.prepare_syntax_dict(syntax=SyntaxType.JSON) if partial.member else None,
                    **(
                        {"blueprint_outcomes": _serialize_blueprint_outcomes(partial.blueprint_outcomes)}
                        if preview
                        else {}
                    ),
                }
                for partial in self.partial
            ],
            "failure": [
                {
                    "id": failure.id,
                    "action": failure.action,
                    "location": "local" if failure.operation is OperationType.PULL else "remote",
                    "transfer": failure.transfer,
                    "object": failure.member.prepare_syntax_dict(syntax=SyntaxType.JSON) if failure.member else None,
                    "reason": failure.reason,
                }
                for failure in self.failure
            ],
            "skipped": [
                {
                    "id": skipped.id,
                    "action": skipped.action,
                    "location": None,
                    "transfer": skipped.transfer,
                    "object": skipped.member.prepare_syntax_dict(syntax=SyntaxType.JSON) if skipped.member else None,
                }
                for skipped in self.skipped
            ],
        }
