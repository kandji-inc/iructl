"""The `iructl app` command group, organized into three roles:

- ``lifecycle`` -- push/pull/sync overrides that add installer upload/download (installer.py).
- standalone subcommands -- new / set-file / show / download.
- shared support -- options.py (option types) and installer.py (payload + binary mechanics).
"""

import typer

from iructl._cli.member import MemberCliDescriptor, register_lifecycle_commands
from iructl.repository import CustomApp

from .download import app as download_app
from .lifecycle import app_pull, app_push, app_sync
from .new import app as new_app
from .set_file import app as set_file_app
from .show import app as show_app

__all__ = ["app"]

descriptor = MemberCliDescriptor(
    member_type=CustomApp,
    noun_singular="app",
    noun_plural="apps",
    group_help="Interact with Iru Custom Apps",
    push_command=app_push,
    pull_command=app_pull,
    sync_command=app_sync,
)

app = typer.Typer(rich_markup_mode="rich")
register_lifecycle_commands(app, descriptor)
app.add_typer(new_app)
app.add_typer(show_app)
app.add_typer(set_file_app)
app.add_typer(download_app)
