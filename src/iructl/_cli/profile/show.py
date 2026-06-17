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
from iructl.repository import CustomProfile

__all__ = ["app"]

console = OutputConsole(logging.getLogger(__name__))

app = typer.Typer(rich_markup_mode="rich")


# --- Show Specific Options ---
ProfileOnlyOption = Annotated[
    bool, typer.Option("--profile", "-p", show_default=False, help="Only show the profile content.")
]
ProfileArgument = Annotated[
    str,
    typer.Argument(
        metavar="PROFILE",
        show_default=False,
        help="ID of or path to the profile to show",
    ),
]
RemoteOption = Annotated[
    bool,
    typer.Option(
        "--remote",
        "-r",
        show_default=False,
        help="Show remote profile instead of local version.",
    ),
]


@app.command(name="show", no_args_is_help=True, epilog=epilog_text)
def show_profile(
    ctx: typer.Context,
    profile_arg: ProfileArgument,
    remote: RemoteOption = False,
    profile_only: ProfileOnlyOption = False,
    format: FormatOption = OutputFormat.TABLE,
    output: OutputOption = "-",
    repo_str: OptionalRepoPathOption = None,
    tenant_url: IruTenantOption = None,
    api_token: ApiTokenOption = None,
):
    """Show details of a custom profile."""

    def project(member: CustomProfile, format: OutputFormat) -> tuple[str | None, SyntaxType | None]:
        if profile_only:
            return member.profile.format_plain_text(format=format), format.to_syntax()
        if format is OutputFormat.TABLE:
            return None, None
        return member.format_plain_text(format=format), format.to_syntax()

    run_show(
        CustomProfile,
        profile_arg,
        remote=remote,
        repo_str=resolve_repo(ctx, repo_str),
        tenant_url=tenant_url,
        api_token=api_token,
        format=format,
        output=output,
        project=project,
    )
