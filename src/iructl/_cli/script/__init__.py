import typer

from iructl._cli.member import MemberCliDescriptor, register_lifecycle_commands
from iructl.repository import CustomScript

from .new import app as new_app
from .show import app as show_app

__all__ = ["app"]

descriptor = MemberCliDescriptor(
    member_type=CustomScript,
    noun_singular="script",
    noun_plural="scripts",
    group_help="Interact with Iru Custom Scripts",
)
app = typer.Typer(rich_markup_mode="rich")
register_lifecycle_commands(app, descriptor)
app.add_typer(new_app)
app.add_typer(show_app)
