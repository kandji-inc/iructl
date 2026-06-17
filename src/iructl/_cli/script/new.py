import logging
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from iructl._cli.common import DeprecatedInfoFormatOption, InfoFormatOption, resolve_info_format
from iructl._cli.utility import finalize_new_member, validate_output_path
from iructl._console import OutputConsole, epilog_text
from iructl._constants import DEFAULT_SCRIPT_CATEGORY
from iructl.exceptions import InvalidScriptError
from iructl.repository import (
    CustomScript,
    ExecutionFrequency,
    InfoFormat,
    RepositoryDirectory,
    Script,
    ScriptInfoFile,
)

__all__ = ["app"]

console = OutputConsole(logging.getLogger(__name__))

app = typer.Typer(rich_markup_mode="rich")


# --- Options ---
ImportAuditOption = Annotated[
    str | None,
    typer.Option(
        "--import-audit",
        "-i",
        help="A path to an audit script to import.",
        rich_help_panel="Input",
        metavar="FILE",
        show_default=False,
    ),
]
IncludeRemediationFlag = Annotated[
    bool,
    typer.Option(
        "--include-remediation",
        "-k",
        help="Include a remediation script.",
        rich_help_panel="Input",
        show_default=False,
    ),
]
ImportRemediationOption = Annotated[
    str | None,
    typer.Option(
        "--import-remediation",
        "-r",
        help="A path to a remediation script to import.",
        rich_help_panel="Input",
        metavar="FILE",
        show_default=False,
    ),
]
NameOption = Annotated[
    str,
    typer.Option(
        "--name",
        "-n",
        help="A name for the script. Also used for the script directory name.",
        prompt="Custom Script Name",
    ),
]
ActiveFlag = Annotated[
    bool,
    typer.Option(
        "--active/--disabled",
        help="Configure the script to be active.",
    ),
]
ExecutionFrequencyOption = Annotated[
    ExecutionFrequency,
    typer.Option(
        "--execution-frequency",
        "-e",
        help="The execution frequency for the script.",
    ),
]
SelfServiceFlag = Annotated[
    bool,
    typer.Option(
        "--self-service",
        "-s",
        help="Configure the script to be available in Self Service.",
    ),
]
SelfServiceCategoryOption = Annotated[
    str | None,
    typer.Option(
        "--category",
        "-c",
        help="The name or id of the script's Self Service category.",
        show_default=False,
    ),
]
SelfServiceRecommendedFlag = Annotated[
    bool,
    typer.Option(
        "--recommended",
        help="Configure the script to be recommended in Self Service.",
    ),
]
RestartOption = Annotated[
    bool,
    typer.Option(
        "--restart/--no-restart",
        help="Restart the computer after running the script.",
    ),
]
OutputOption = Annotated[
    str | None,
    typer.Option(
        "--output",
        "-o",
        help="A path to a directory where the new script directory should be created. This path must be in a scripts repository.",
        rich_help_panel="Output",
        metavar="DIRECTORY",
        show_default=False,
    ),
]
CopyModeFlag = Annotated[
    bool,
    typer.Option(
        "--copy/--move",
        show_default="copy",
        rich_help_panel="Input",
        help="The action to take when importing a script.",
    ),
]


@app.command(name="new", epilog=epilog_text)
def new_script(
    ctx: typer.Context,
    name: NameOption,
    copy_mode: CopyModeFlag = True,
    active: ActiveFlag = False,
    execution_frequency: ExecutionFrequencyOption = ExecutionFrequency.ONCE,
    restart: RestartOption = False,
    self_service: SelfServiceFlag = False,
    self_service_category: SelfServiceCategoryOption = None,
    self_service_recommended: SelfServiceRecommendedFlag = False,
    audit_path_str: ImportAuditOption = None,
    remediation_path_str: ImportRemediationOption = None,
    include_remediation: IncludeRemediationFlag = False,
    output: OutputOption = None,
    format: InfoFormatOption = InfoFormat.PLIST,
    deprecated_format: DeprecatedInfoFormatOption = None,
):
    """Create or import a new custom script.

    If a script is provided via --import-audit or --import-remediation, the script(s)
    will be imported. Otherwise, a new no-op audit script will be created.
    """

    if audit_path_str is None and remediation_path_str is not None:
        msg = "A remediation script cannot be imported without an audit script."
        console.error(msg)
        raise typer.BadParameter(msg)

    # Determine the output path for the new script and fail fast if it's invalid
    output_path = validate_output_path(directory=RepositoryDirectory.SCRIPTS, override=output, repo=ctx.obj.repo)

    random_id = str(uuid4())
    info_data = {
        "id": random_id,
        "name": name,
        "active": active,
        "execution_frequency": execution_frequency,
        "restart": restart,
    }

    # If any self service options are provided, set the self service fields. Defaults are set to make editing easier.
    # Category names are allowed as a convenience. The field will be updated to the category ID when pushed.
    if (
        execution_frequency == ExecutionFrequency.NO_ENFORCEMENT
        or self_service is True
        or self_service_category is not None
        or self_service_recommended is True
    ):
        info_data["show_in_self_service"] = True  # Required for all other self service options
        info_data["self_service_category_id"] = (
            DEFAULT_SCRIPT_CATEGORY if self_service_category is None else self_service_category
        )
        info_data["self_service_recommended"] = self_service_recommended

    # If an audit script path is provided, load it, otherwise create a new empty script
    if audit_path_str is not None:
        audit_path = Path(audit_path_str).expanduser().resolve()
        if not audit_path.is_file():
            msg = f"The path provided for --import-audit option does not exist. (got {audit_path})"
            console.error(msg)
            raise typer.BadParameter(msg)
        try:
            audit_script = Script.load(audit_path)
        except InvalidScriptError as error:
            console.error(f"Failed to load audit script data from {audit_path}: {error}")
            raise typer.BadParameter(f"The audit script ({audit_path}) is invalid. Check the file and try again.")
    else:
        audit_script = Script(content=Script.default_content)
        audit_path = None

    # If a remediation script path is provided, load it
    if remediation_path_str is not None:
        remediation_path = Path(remediation_path_str).expanduser().resolve()
        if not remediation_path.is_file():
            msg = f"The path provided for --import-remediation option does not exist. (got {remediation_path})"
            console.error(msg)
            raise typer.BadParameter(msg)
        try:
            remediation_script = Script.load(remediation_path)
        except InvalidScriptError as error:
            console.error(f"Failed to load remediation script data from {remediation_path}: {error}")
            raise typer.BadParameter(
                f"The remediation script ({remediation_path}) is invalid. Check the file and try again."
            )
    elif include_remediation:
        remediation_script = Script(content=Script.default_content)
        remediation_path = None
    else:
        remediation_script = None
        remediation_path = None

    info_file = ScriptInfoFile.model_validate(info_data)
    info_file.format = resolve_info_format(format, deprecated_format)

    custom_script = CustomScript(info=info_file, audit=audit_script, remediation=remediation_script)

    sources = {attr: path for attr, path in {"audit": audit_path, "remediation": remediation_path}.items() if path}
    finalize_new_member(custom_script, output_path, sources=sources, copy_mode=copy_mode)

    if sources:
        console.print_success(f"Script imported at {custom_script.info_path.parent}")
    else:
        console.print_success(f"New script created at {custom_script.info_path.parent}")
