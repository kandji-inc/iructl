import logging
import os
import shutil
from importlib.resources import read_text
from pathlib import Path
from typing import Annotated

import typer

from iructl import _git
from iructl._cli.payloads import _GITIGNORE_CONTENTS, resolve_payload_dir
from iructl._config import load_user_config
from iructl._console import OutputConsole, epilog_text
from iructl._constants import (
    APP_BRANDING,
    APP_NAME,
    PAYLOAD_DIR_ENV,
    ROOT_MARKER,
    TENANT_ENV,
    TOKEN_ENV,
)
from iructl._utils import locate_repo_root
from iructl.exceptions import GitRepositoryError, InvalidRepositoryError
from iructl.repository import RepositoryDirectory

__all__ = ["app"]

console = OutputConsole(logging.getLogger(__name__))

app = typer.Typer(rich_markup_mode="rich")

readme_text = (
    read_text("iructl._cli._resources", "new_repo_readme.md", encoding="utf-8")
    .replace("{APP_NAME}", APP_NAME)
    .replace("{APP_BRANDING}", APP_BRANDING)
    .replace("{TENANT_ENV}", TENANT_ENV)
    .replace("{TOKEN_ENV}", TOKEN_ENV)
    .replace("{PAYLOAD_DIR_ENV}", PAYLOAD_DIR_ENV)
)
macos_gitignore_text = read_text("iructl._cli._resources", "gitignore.txt", encoding="utf-8")

PathOption = Annotated[
    str,
    typer.Argument(
        metavar="PATH",
        resolve_path=True,
        show_default=False,
        help="A Path to the directory where the new repository should be initialized.",
    ),
]


@app.command(name="new", epilog=epilog_text, no_args_is_help=True)
def new_repo(ctx: typer.Context, path_str: PathOption):
    """Create a new repository"""

    git_enabled = ctx.obj.git

    # Check if the path already exists and raise an error if it does.
    path = Path(path_str).expanduser().resolve()
    if path.exists():
        msg = f"Path {path} already exists."
        console.error(msg)
        raise typer.BadParameter(msg)

    # Reject nesting inside an existing iructl repo, independent of the git-tree lookup.
    git_root = None
    try:
        if git_enabled:
            git_root = _git.locate_git_root(cd_path=path)
    except InvalidRepositoryError:
        pass

    try:
        repo_root = locate_repo_root(cd_path=path)
    except InvalidRepositoryError:
        pass
    else:
        msg = f"The parent path {repo_root} is already a {APP_BRANDING} repository. Please choose another path."
        console.error(msg)
        raise typer.BadParameter(msg)

    # Create the directory and initial files. The .gitignore dotfiles are git-only, so skip them under --no-git.
    path.mkdir(parents=True)
    (path / ROOT_MARKER).touch()
    (path / "README.md").write_text(readme_text, encoding="utf-8")
    if git_enabled:
        (path / ".gitignore").write_text(macos_gitignore_text, encoding="utf-8")
    (path / RepositoryDirectory.PROFILES).mkdir()
    (path / RepositoryDirectory.SCRIPTS).mkdir()
    (path / RepositoryDirectory.APPS).mkdir()
    payload_dir = resolve_payload_dir(path, os.environ.get(PAYLOAD_DIR_ENV) or load_user_config().payload_dir)
    if payload_dir.is_relative_to(path):
        payload_dir.mkdir(parents=True, exist_ok=True)
        if git_enabled:
            (payload_dir / ".gitignore").write_text(_GITIGNORE_CONTENTS, encoding="utf-8")

    if git_enabled:
        try:
            if git_root is None:
                _git.git("init", cd_path=path, expected_exit_code=0)
                commit_msg = "Initial commit"
            else:
                commit_msg = f"Create {APP_NAME} repository files"
            _git.commit_all_changes(cd_path=path, message=commit_msg)
        except FileNotFoundError:
            console.print_error(
                f"A suitable Git executable was not found. Git is required for managing {APP_BRANDING} repositories. Please ensure Git is installed."
            )
            shutil.rmtree(path)
            raise typer.Exit(code=1)
        except GitRepositoryError:
            console.print_error("Failed to initialize the repository. Please check the log for more information.")
            shutil.rmtree(path)
            raise typer.Exit(code=1)

    console.print_success(f"Created a new {APP_BRANDING} repository at {path}")
    console.print("Check out the included README.md file for more information on getting started.")
