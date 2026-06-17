import logging
from enum import StrEnum
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from iructl._cli.common import DeprecatedInfoFormatOption, InfoFormatOption, resolve_info_format
from iructl._cli.utility import finalize_new_member, validate_output_path
from iructl._console import OutputConsole, epilog_text
from iructl.exceptions import InvalidProfileError
from iructl.repository import CustomProfile, InfoFormat, Mobileconfig, ProfileInfoFile, RepositoryDirectory

__all__ = ["app"]

console = OutputConsole(logging.getLogger(__name__))

app = typer.Typer(rich_markup_mode="rich")


class RunsOn(StrEnum):
    MAC = "mac"
    IPHONE = "iphone"
    IPAD = "ipad"
    TV = "tv"
    VISION = "vision"
    ALL = "all"


# --- Options ---
ImportOption = Annotated[
    str | None,
    typer.Option(
        "--import",
        "-i",
        help="A path to a mobileconfig profile to import.",
        rich_help_panel="Input",
        metavar="FILE",
        show_default=False,
    ),
]
NameOption = Annotated[
    str | None,
    typer.Option(
        "--name",
        "-n",
        help="A name for the profile. Also used for the profile directory name.",
        show_default=False,
    ),
]
RunsOnOption = Annotated[
    list[RunsOn],
    typer.Option(
        "--runs-on",
        "-r",
        help="The device type the profile targets. Can be provided multiple times.",
    ),
]
ActiveFlag = Annotated[
    bool,
    typer.Option(
        "--active/--disabled",
        help="Configure the profile to be active when uploaded to Iru.",
    ),
]
OutputOption = Annotated[
    str | None,
    typer.Option(
        "--output",
        "-o",
        help="A path to a directory where the new profile directory should be created. This path must be in a profiles repository.",
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
        help="The action to take when importing a profile.",
    ),
]


@app.command(name="new", epilog=epilog_text)
def new_profile(
    ctx: typer.Context,
    name: NameOption = None,
    import_profile: ImportOption = None,
    copy_mode: CopyModeFlag = True,
    runs_on: RunsOnOption = [RunsOn.ALL],
    active: ActiveFlag = False,
    output: OutputOption = None,
    format: InfoFormatOption = InfoFormat.PLIST,
    deprecated_format: DeprecatedInfoFormatOption = None,
):
    """Create or import a new custom profile.

    If a profile is provided via --import, the profile will be imported and the
    necessary data extracted. Otherwise, a new empty profile will be created.
    """

    if import_profile is not None:
        import_profile_path = Path(import_profile).expanduser().resolve()
        if not import_profile_path.is_file():
            msg = f"The path provided for --import option does not exist. (got {import_profile_path})"
            console.error(msg)
            raise typer.BadParameter(msg)
    else:
        import_profile_path = None

    # Determine the output path for the new profile and fail fast if it's invalid
    output_path = validate_output_path(directory=RepositoryDirectory.PROFILES, override=output, repo=ctx.obj.repo)

    # If RunsOn.ALL is included, remove it and add all other RunOn values
    runs_on = [r for r in RunsOn if r is not RunsOn.ALL] if RunsOn.ALL in runs_on else runs_on

    random_id = str(uuid4())
    info_data = {"id": random_id, "mdm_identifier": f"com.kandji.profile.custom.{random_id}", "active": active} | {
        f"runs_on_{r.value}": True for r in runs_on
    }

    # If a profile is provided, load it and extract the necessary data, otherwise create a new empty profile
    if import_profile_path is not None:
        try:
            mobileconfig = Mobileconfig.load(import_profile_path)
        except InvalidProfileError as error:
            console.error(f"Failed to load profile data from {import_profile_path}: {error}")
            raise typer.BadParameter(
                f"The profile ({import_profile_path}) is in an invalid format. Check the file and try again."
            )

        if name is None:
            name = mobileconfig.data.get("PayloadDisplayName", None)
    else:
        mobileconfig = None

    info_data["name"] = typer.prompt("Custom Profile Name") if name is None else name

    if mobileconfig is None:
        mobileconfig = Mobileconfig(content=Mobileconfig.default_content(_id=random_id, name=info_data["name"]))

    info_file = ProfileInfoFile.model_validate(info_data)
    info_file.format = resolve_info_format(format, deprecated_format)

    custom_profile = CustomProfile(info=info_file, profile=mobileconfig)

    sources = {"profile": import_profile_path} if import_profile_path is not None else {}
    finalize_new_member(custom_profile, output_path, sources=sources, copy_mode=copy_mode)

    if import_profile_path is not None:
        console.print_success(f"Profile imported at {custom_profile.profile_path.parent}")
    else:
        console.print_success(f"New profile created at {custom_profile.profile_path.parent}")
