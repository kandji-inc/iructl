import logging
from typing import Annotated

import typer

from iructl._cli.common import (
    ApiTokenOption,
    FormatOption,
    IruTenantOption,
    OptionalRepoPathOption,
    OutputOption,
    resolve_repo,
)
from iructl._cli.utility import run_show
from iructl._console import OutputConsole, OutputFormat, SyntaxType, epilog_text
from iructl.repository import CustomScript

__all__ = ["app"]

console = OutputConsole(logging.getLogger(__name__))

app = typer.Typer(rich_markup_mode="rich")


# --- Show Specific Options ---
AuditOnlyOption = Annotated[
    bool, typer.Option("--audit", "-a", show_default=False, help="Only show the audit script content.")
]
RemediationOnlyOption = Annotated[
    bool,
    typer.Option("--remediation", "-k", show_default=False, help="Only show the remediation script content."),
]
ScriptArgument = Annotated[
    str,
    typer.Argument(
        metavar="SCRIPT",
        show_default=False,
        help="ID of or path to the script to show",
    ),
]
RemoteOption = Annotated[
    bool,
    typer.Option(
        "--remote",
        "-r",
        show_default=False,
        help="Show remote script instead of local version.",
    ),
]


@app.command(name="show", no_args_is_help=True, epilog=epilog_text)
def show_script(
    ctx: typer.Context,
    script_arg: ScriptArgument,
    remote: RemoteOption = False,
    audit_only: AuditOnlyOption = False,
    remediation_only: RemediationOnlyOption = False,
    format: FormatOption = OutputFormat.TABLE,
    output: OutputOption = "-",
    repo_str: OptionalRepoPathOption = None,
    tenant_url: IruTenantOption = None,
    api_token: ApiTokenOption = None,
):
    """Show details of a custom script."""

    if audit_only and remediation_only:
        msg = "--audit-only and --remediation-only are mutually exclusive. Please choose one."
        console.error(msg)
        raise typer.BadParameter(msg)

    def project(member: CustomScript, format: OutputFormat) -> tuple[str | None, SyntaxType | None]:
        if audit_only:
            return member.audit.content, None
        if remediation_only:
            return (member.remediation.content if member.remediation else ""), None
        if format is OutputFormat.TABLE:
            return None, None
        return member.format_plain_text(format), format.to_syntax()

    run_show(
        CustomScript,
        script_arg,
        remote=remote,
        repo_str=resolve_repo(ctx, repo_str),
        tenant_url=tenant_url,
        api_token=api_token,
        format=format,
        output=output,
        project=project,
    )
