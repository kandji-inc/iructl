import logging
import logging.handlers
from pathlib import Path
from typing import Annotated

import typer

from iructl.__about__ import __version__
from iructl._config import build_default_map, load_repo_config, load_user_config, merge_configs
from iructl._console import OutputConsole, epilog_text
from iructl._constants import APP_BRANDING, APP_NAME, DEBUG_ENV, IS_KST, LOG_FILE

from .app import app as app_app
from .common import GitFlag, GlobalState, PreviewOption, RepoPathOption, option_was_set
from .new import app as new_app
from .profile import app as profile_app
from .script import app as script_app

__all__ = ["app"]

console = OutputConsole(logging.getLogger(__name__))

app = typer.Typer(name=APP_NAME, rich_markup_mode="rich", pretty_exceptions_show_locals=False)
app.add_typer(new_app)
if not IS_KST:
    app.add_typer(
        app_app,
        name="app",
        epilog=epilog_text,
        no_args_is_help=True,
    )
app.add_typer(
    profile_app,
    name="profile",
    epilog=epilog_text,
    no_args_is_help=True,
)
app.add_typer(
    script_app,
    name="script",
    epilog=epilog_text,
    no_args_is_help=True,
)


def version_callback(value: bool) -> None:
    """Callback for the --version flag."""
    if value:
        console.print(f"{APP_NAME}, version {__version__}")
        raise typer.Exit(code=0)


VersionFlag = Annotated[
    bool,
    typer.Option(
        "--version",
        show_default=False,
        help="Show the version.",
        callback=version_callback,
        is_eager=True,
    ),
]
LogPathOption = Annotated[
    str,
    typer.Option(
        "--log",
        show_default=False,
        help="Path to the log file. (use '-' for stdout)",
        rich_help_panel="Logging",
        resolve_path=True,
        allow_dash=True,
    ),
]
DebugFlag = Annotated[
    bool,
    typer.Option(
        "--debug",
        envvar=DEBUG_ENV,
        help="Enable debug logging.",
        rich_help_panel="Logging",
    ),
]


def _resolve_root_setting(ctx: typer.Context, name: str, flag_value: bool, config_value: bool | None) -> bool:
    """Resolve a root-callback flag against the layered config.

    ``default_map`` reaches only subcommand params, so root-callback flags fall back
    to config here: the config value wins only when the flag is left at its default.
    """
    if option_was_set(ctx, name) or config_value is None:
        return flag_value
    return config_value


@app.callback(
    no_args_is_help=True,
    epilog=epilog_text,
    help=f"{APP_BRANDING} - a utility for local management of Iru resources.",
)
def main(
    ctx: typer.Context,
    repo: RepoPathOption = ".",
    git: GitFlag = True,
    log: LogPathOption = str(LOG_FILE),
    debug: DebugFlag = False,
    preview: PreviewOption = False,
    version: VersionFlag = False,  # noqa: ARG001
) -> None:
    if log == "-":
        handlers = [logging.StreamHandler()]
    else:
        log_path = Path(log).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers: list[logging.Handler] = [
            logging.handlers.RotatingFileHandler(filename=log_path, maxBytes=5120000, backupCount=3)
        ]

    # Layer config (user < repo .iructl) beneath CLI flags and env vars via Click's default_map.
    merged = merge_configs(load_user_config(), load_repo_config(Path(repo)))

    debug = _resolve_root_setting(ctx, "debug", debug, merged.debug)

    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )
    console.info(f"--- Starting {APP_BRANDING} ---")

    ctx.default_map = build_default_map(ctx.command, merged)

    preview = _resolve_root_setting(ctx, "preview", preview, merged.preview)
    git = _resolve_root_setting(ctx, "git", git, merged.git_enabled)
    ctx.obj = GlobalState(preview=preview, repo=repo, git=git)
