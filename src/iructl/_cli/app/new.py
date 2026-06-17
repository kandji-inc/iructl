"""The `app new` command: create a local custom-app member, importing its installer into the payload dir."""

import logging
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
from pydantic import ValidationError

from iructl._cli.common import DeprecatedInfoFormatOption, InfoFormatOption, resolve_info_format
from iructl._cli.payloads import protect_payload_dir, resolve_payload_dir
from iructl._cli.utility import finalize_new_member, validate_output_path
from iructl._console import OutputConsole, epilog_text
from iructl._constants import DEFAULT_APP_CATEGORY
from iructl._utils import locate_repo_root, validation_error_messages
from iructl.exceptions import InvalidScriptError
from iructl.repository import (
    AppInfoFile,
    CustomApp,
    InfoFormat,
    InstallEnforcement,
    InstallType,
    RepositoryDirectory,
    Script,
)

from .installer import import_installer
from .options import PayloadDirInputOption

__all__ = ["app"]

console = OutputConsole(logging.getLogger(__name__))

app = typer.Typer(rich_markup_mode="rich")

_EXTENSION_INSTALL_TYPES = {
    ".pkg": InstallType.PACKAGE,
    ".zip": InstallType.ZIP,
    ".dmg": InstallType.IMAGE,
}


# --- Options ---
NameOption = Annotated[
    str | None,
    typer.Option("--name", "-n", show_default=False, help="A name for the app. Defaults to the installer file name."),
]
FileOption = Annotated[
    str,
    typer.Option(
        "--file",
        help="Path to the installer file to import into the payload directory.",
        rich_help_panel="Input",
        metavar="PATH",
    ),
]
InstallTypeOption = Annotated[
    InstallType | None,
    typer.Option(
        "--installer-type",
        "-t",
        show_default=False,
        help="The installer type. Auto-detected from the file extension when omitted.",
    ),
]
InstallEnforcementOption = Annotated[
    InstallEnforcement,
    typer.Option("--enforcement", "-e", help="The install enforcement type."),
]
RestartOption = Annotated[bool, typer.Option("--restart/--no-restart", help="Restart after installation.")]
UnzipLocationOption = Annotated[
    str | None,
    typer.Option("--unzip-location", show_default=False, help="Unzip destination (required for zip install type)."),
]
ActiveFlag = Annotated[bool, typer.Option("--active/--disabled", help="Configure the app to be active.")]
SelfServiceFlag = Annotated[bool, typer.Option("--self-service", "-s", help="Show the app in Self Service.")]
SelfServiceCategoryOption = Annotated[
    str | None,
    typer.Option("--category", "-c", show_default=False, help="Name or id of the app's Self Service category."),
]
SelfServiceRecommendedFlag = Annotated[bool, typer.Option("--recommended", help="Recommend the app in Self Service.")]
ImportAuditOption = Annotated[
    str | None,
    typer.Option(
        "--import-audit",
        show_default=False,
        rich_help_panel="Input",
        metavar="FILE",
        help="Path to an audit script to import.",
    ),
]
ImportPreinstallOption = Annotated[
    str | None,
    typer.Option(
        "--import-preinstall",
        show_default=False,
        rich_help_panel="Input",
        metavar="FILE",
        help="Path to a preinstall script.",
    ),
]
ImportPostinstallOption = Annotated[
    str | None,
    typer.Option(
        "--import-postinstall",
        show_default=False,
        rich_help_panel="Input",
        metavar="FILE",
        help="Path to a postinstall script.",
    ),
]
CopyModeFlag = Annotated[
    bool,
    typer.Option(
        "--copy/--move",
        show_default="copy",
        rich_help_panel="Input",
        help="Copy or move the installer and any imported scripts into the repo.",
    ),
]
OutputOption = Annotated[
    str | None,
    typer.Option(
        "--output",
        "-o",
        show_default=False,
        rich_help_panel="Output",
        metavar="DIRECTORY",
        help="Output directory in an apps repo.",
    ),
]


def _load_script(path_str: str, label: str) -> tuple[Script, Path]:
    path = Path(path_str).expanduser().resolve()
    if not path.is_file():
        msg = f"The path provided for --import-{label} does not exist. (got {path})"
        console.error(msg)
        raise typer.BadParameter(msg)
    try:
        return Script.load(path), path
    except InvalidScriptError as error:
        console.error(f"Failed to load {label} script from {path}: {error}")
        raise typer.BadParameter(f"The {label} script ({path}) is invalid. Check the file and try again.")


@app.command(name="new", epilog=epilog_text)
def new_app(
    ctx: typer.Context,
    file: FileOption,
    name: NameOption = None,
    copy_mode: CopyModeFlag = True,
    active: ActiveFlag = False,
    install_type: InstallTypeOption = None,
    install_enforcement: InstallEnforcementOption = InstallEnforcement.NO_ENFORCEMENT,
    unzip_location: UnzipLocationOption = None,
    restart: RestartOption = False,
    self_service: SelfServiceFlag = False,
    self_service_category: SelfServiceCategoryOption = None,
    self_service_recommended: SelfServiceRecommendedFlag = False,
    import_audit: ImportAuditOption = None,
    import_preinstall: ImportPreinstallOption = None,
    import_postinstall: ImportPostinstallOption = None,
    payload_dir: PayloadDirInputOption = None,
    output: OutputOption = None,
    format: InfoFormatOption = InfoFormat.PLIST,
    deprecated_format: DeprecatedInfoFormatOption = None,
):
    """Create a new custom app, importing the installer at --file into the payload directory."""

    if import_audit is not None and install_enforcement is not InstallEnforcement.CONTINUOUSLY_ENFORCE:
        msg = "An audit script requires --enforcement continuously_enforce."
        console.error(msg)
        raise typer.BadParameter(msg)

    output_path = validate_output_path(directory=RepositoryDirectory.APPS, override=output, repo=ctx.obj.repo)
    repo_root = locate_repo_root(cd_path=output_path)
    payload_path = resolve_payload_dir(repo_root, payload_dir)

    source = Path(file).expanduser().resolve()
    if not source.is_file():
        msg = f"The installer file does not exist. (got {source})"
        console.error(msg)
        raise typer.BadParameter(msg)

    if name is None:
        name = source.stem

    if install_type is None:
        install_type = _EXTENSION_INSTALL_TYPES.get(source.suffix.lower())
        if install_type is None:
            msg = f"Could not determine the installer type from '{source.name}'. Specify it with --installer-type."
            console.error(msg)
            raise typer.BadParameter(msg)

    if install_type is InstallType.ZIP and unzip_location is None:
        msg = "Unzip destination is required for the zip install type. Pass --unzip-location."
        console.error(msg)
        raise typer.BadParameter(msg)
    if install_type is not InstallType.ZIP and unzip_location is not None:
        msg = "--unzip-location is only valid for the zip install type."
        console.error(msg)
        raise typer.BadParameter(msg)

    file_name, installer_sha = import_installer(source, payload_path, copy_mode=copy_mode)

    if ctx.obj.git:
        protect_payload_dir(payload_path, repo_root)

    info_data: dict = {
        "id": str(uuid4()),
        "name": name,
        "active": active,
        "install_type": install_type,
        "install_enforcement": install_enforcement,
        "restart": restart,
        "unzip_location": unzip_location,
        "file": {"name": file_name, "sha256": installer_sha},
    }
    if (
        install_enforcement is InstallEnforcement.NO_ENFORCEMENT
        or self_service
        or self_service_category is not None
        or self_service_recommended
    ):
        info_data["show_in_self_service"] = True
        info_data["self_service_category_id"] = self_service_category or DEFAULT_APP_CATEGORY
        info_data["self_service_recommended"] = self_service_recommended

    try:
        info_file = AppInfoFile.model_validate(info_data)
    except ValidationError as error:
        message = "; ".join(validation_error_messages(error))
        console.error(message)
        raise typer.BadParameter(message) from error
    info_file.format = resolve_info_format(format, deprecated_format)

    imports = {"audit": import_audit, "preinstall": import_preinstall, "postinstall": import_postinstall}
    loaded = {attr: _load_script(path_str, attr) for attr, path_str in imports.items() if path_str is not None}

    custom_app = CustomApp(
        info=info_file,
        audit=loaded["audit"][0] if "audit" in loaded else None,
        preinstall=loaded["preinstall"][0] if "preinstall" in loaded else None,
        postinstall=loaded["postinstall"][0] if "postinstall" in loaded else None,
    )

    sources = {attr: source_path for attr, (_, source_path) in loaded.items()}
    finalize_new_member(custom_app, output_path, sources=sources, copy_mode=copy_mode)

    console.print_success(f"New app created at {custom_app.info_path.parent}")
