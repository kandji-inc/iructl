"""The `app show` command: render a single custom app."""

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
from iructl.repository import CustomApp

__all__ = ["app"]

console = OutputConsole(logging.getLogger(__name__))

app = typer.Typer(rich_markup_mode="rich")

AppArgument = Annotated[
    str,
    typer.Argument(metavar="APP", show_default=False, help="ID of or path to the app to show."),
]
RemoteOption = Annotated[
    bool,
    typer.Option("--remote", "-r", show_default=False, help="Show the remote app instead of the local version."),
]
ScriptOnlyOption = Annotated[
    str | None,
    typer.Option(
        "--script",
        "-s",
        show_default=False,
        help="Only show one script's content: audit, preinstall, or postinstall.",
    ),
]


@app.command(name="show", no_args_is_help=True, epilog=epilog_text)
def show_app(
    ctx: typer.Context,
    app_arg: AppArgument,
    remote: RemoteOption = False,
    script: ScriptOnlyOption = None,
    format: FormatOption = OutputFormat.TABLE,
    output: OutputOption = "-",
    repo_str: OptionalRepoPathOption = None,
    tenant_url: IruTenantOption = None,
    api_token: ApiTokenOption = None,
):
    """Show details of a custom app."""

    if script is not None and script not in {"audit", "preinstall", "postinstall"}:
        msg = "--script must be one of: audit, preinstall, postinstall."
        console.error(msg)
        raise typer.BadParameter(msg)

    def project(member: CustomApp, format: OutputFormat) -> tuple[str | None, SyntaxType | None]:
        if script is not None:
            child = getattr(member, script)
            return (child.content if child is not None else ""), None
        if format is OutputFormat.TABLE:
            return None, None
        return member.format_plain_text(format), format.to_syntax()

    run_show(
        CustomApp,
        app_arg,
        remote=remote,
        repo_str=resolve_repo(ctx, repo_str),
        tenant_url=tenant_url,
        api_token=api_token,
        format=format,
        output=output,
        project=project,
    )
