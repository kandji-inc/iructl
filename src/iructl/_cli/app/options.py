"""Options shared across the custom-app commands."""

from typing import Annotated

import typer

from iructl._constants import PAYLOAD_DIR_ENV

_PAYLOAD_DIR_HELP = (
    f"Directory holding installer binaries, relative to the repo root "
    f"(default: <repo>/payloads, or ${PAYLOAD_DIR_ENV})."
)

PayloadDirOption = Annotated[
    str | None,
    typer.Option(
        "--payload-dir",
        show_default=False,
        envvar=PAYLOAD_DIR_ENV,
        metavar="DIRECTORY",
        help=_PAYLOAD_DIR_HELP,
    ),
]
# Same option, grouped under the Input panel for the new command (where the payload dir is the installer source).
PayloadDirInputOption = Annotated[
    str | None,
    typer.Option(
        "--payload-dir",
        show_default=False,
        envvar=PAYLOAD_DIR_ENV,
        rich_help_panel="Input",
        metavar="DIRECTORY",
        help=_PAYLOAD_DIR_HELP,
    ),
]
DownloadFlag = Annotated[
    bool,
    typer.Option("--download", help="Download each app's installer binary to the payload directory."),
]
