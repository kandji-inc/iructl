import functools
import logging
import shutil
import subprocess
from collections import defaultdict
from enum import StrEnum
from pathlib import Path

from iructl._console import OutputConsole
from iructl._constants import APP_NAME, PROFILES_DIR, SCRIPTS_DIR
from iructl._utils import locate_repo_root, nearest_existing_dir
from iructl.exceptions import GitRepositoryError, InvalidRepositoryError

console = OutputConsole(logging.getLogger(__name__))


@functools.cache
def locate_git() -> str:
    """Locate the git executable.

    This function is only executed once per run and the result is cached for any
    subsequent calls.

    Returns:
        Path: The path to the git executable.

    Raises:
        FileNotFoundError: If the git executable is not found

    """

    git_path = shutil.which("git")
    if git_path is None:
        console.error("Failed to locate the git executable.")
        raise FileNotFoundError("Failed to locate the git executable.")
    try:
        # Check that the git executable is working. This may not be the case on macOS systems before CommandLineTools are installed.
        result = subprocess.run([git_path, "--version"], check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as error:
        console.error(f"git execution failed using {git_path}.")
        raise FileNotFoundError(f"Git execution yielded unexpected result: {error.stderr}") from error
    console.debug(f"Located git executable at {git_path}: {result.stdout.strip()}")
    return git_path


@functools.cache
def has_git_user_config(cd_path: Path | None = None, git_path: str | None = None) -> bool:
    """Check if the git user config is set."""

    cmd = [git_path or locate_git()]
    if cd_path:
        cmd.extend(["-C", str(cd_path)])

    try:
        subprocess.run(
            [*cmd, "config", "--get", "user.name"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        subprocess.run(
            [*cmd, "config", "--get", "user.email"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        return False
    return True


def git(
    *args: str,
    cd_path: Path | None = None,
    git_path: str | None = None,
    expected_exit_code: int | set[int] | None = None,
) -> subprocess.CompletedProcess:
    """Run a git command and return the result.

    Args:
        args (str): The arguments to pass to the git command.
        cd_path (Path): The path to run the git command from.
        git_path (str): The path to the git executable.
        expected_exit_code (int | set[int] | None): Exit code(s) treated as success;
            pass a collection (e.g. {0, 1}) when more than one outcome is valid. If None,
            the code is not checked. Any code outside the set is logged at warning and raises.

    Raises:
        FileNotFoundError: If the git executable is not found.
        GitRepositoryError: If the command fails.

    """

    cmd: list[str] = [git_path or locate_git()]
    console.debug(f"Using git executable at {cmd}")

    if cd_path:
        console.debug(f"Setting CWD for git command to {cd_path}")
        cmd.extend(["-C", str(cd_path)])

    if not has_git_user_config(cd_path):
        console.debug("Git user config not set. Setting temporary user.name and user.email.")
        cmd.extend(["-c", f"user.name={APP_NAME}", "-c", f"user.email={APP_NAME}@{APP_NAME}.invalid"])

    cmd.extend(args)
    console.debug(f"Executing git command: {' '.join(cmd)}")

    result = subprocess.run(cmd, check=False, text=True, capture_output=True)
    console.debug(f"Git command executed with exit code {result.returncode}")

    acceptable = {expected_exit_code} if isinstance(expected_exit_code, int) else expected_exit_code
    if acceptable is not None and result.returncode not in acceptable:
        console.debug(f"Git command exit code ({result.returncode}) was not in expected codes ({acceptable}).")
        console.warning(f"Git command stdout: {result.stdout.strip()}")
        console.warning(f"Git command stderr: {result.stderr.strip()}")
        raise GitRepositoryError(f"Git command failed (exitcode {result.returncode}): {' '.join(args)}")

    console.debug(f"Git command stdout: {result.stdout.strip()}")
    console.debug(f"Git command stderr: {result.stderr.strip()}")
    return result


@functools.cache
def locate_git_root(*, cd_path: Path = Path(".")) -> Path:
    """Locate the root of the working tree containing cd_path via ``git rev-parse``.

    The result is cached to avoid repeated lookups of the same path.

    Args:
        cd_path (Path): The path to run the command from.

    Returns:
        Path: The working-tree root.

    Raises:
        FileNotFoundError: If the git executable is not found.
        InvalidRepositoryError: If cd_path is not part of a valid repository.

    """

    existing_dir = nearest_existing_dir(cd_path)
    console.debug(f"Starting git repository search from {existing_dir}")
    try:
        result = git("rev-parse", "--show-toplevel", cd_path=existing_dir, expected_exit_code=0)
    except GitRepositoryError as error:
        msg = f"{cd_path} is not part of a valid Git repository."
        console.error(msg)
        raise InvalidRepositoryError(msg) from error

    git_root = Path(result.stdout.strip()).expanduser().resolve()
    console.debug(f"Located git repository root at {git_root}")
    return git_root


class GitStatus(StrEnum):
    """Git status enum."""

    ADDED = "Added"
    COPIED = "Copied"
    DELETED = "Deleted"
    MODIFIED = "Modified"
    RENAMED = "Renamed"
    TYPE_CHANGED = "Type changed"
    UNMERGED = "Unmerged"
    UNKNOWN = "Unknown"
    BROKEN = "Broken"

    @classmethod
    def from_status(cls, status: str) -> "GitStatus":
        """Convert a git status string to a GitStatus enum."""
        if status == "A":
            return cls.ADDED
        elif status == "C":
            return cls.COPIED
        elif status == "D":
            return cls.DELETED
        elif status == "M":
            return cls.MODIFIED
        elif status == "R":
            return cls.RENAMED
        elif status == "T":
            return cls.TYPE_CHANGED
        elif status == "U":
            return cls.UNMERGED
        else:
            return cls.UNKNOWN


StatusPath = tuple[GitStatus, Path]


def changed_paths(*, cd_path: Path = Path("."), stage: bool = False, scope: Path | None = None) -> list[StatusPath]:
    """Get a list of changed paths in the repository.

    Args:
        cd_path (Path): The path to run the git command from.
        stage (bool): If True, return staged changes.
        scope (Path | None): If provided, only return changes within this path.

    Returns:
        list[Path]: A list of changed paths.

    Raises:
        GitRepositoryError: If the command fails.

    """

    cmd = ["diff", "--name-status"]
    if stage:
        cmd.append("--staged")
    if scope is not None:
        cmd.extend(["--", str(scope)])

    result = git(*cmd, cd_path=cd_path, expected_exit_code=0)

    return [
        # git returns a list of paths relative to the cd_path. These can include "../".
        (GitStatus.from_status(status), cd_path / path)
        for status, path in (line.split(maxsplit=1) for line in result.stdout.splitlines() if line.strip())
    ]


def generate_commit_body(repo: Path, stage: bool = False) -> str:
    """Generate a commit body for iructl operations."""
    changed = {
        "Profiles": defaultdict[str, set[Path]](set),
        "Scripts": defaultdict[str, set[Path]](set),
        "Other": defaultdict[str, set[Path]](set),
    }

    git_root = locate_git_root(cd_path=repo)
    repo_root = locate_repo_root(cd_path=repo)
    commit_body = ""
    for status, path in changed_paths(cd_path=git_root, stage=stage, scope=repo_root):
        if path.is_relative_to(repo_root / PROFILES_DIR):
            changed["Profiles"][status].add(path)
        elif path.is_relative_to(repo_root / SCRIPTS_DIR):
            changed["Scripts"][status].add(path)
        else:
            changed["Other"][status].add(path)

    for key, statuses in changed.items():
        for status, paths in statuses.items():
            commit_body += f"--- {key} {status} ---\n"
            for path in sorted(paths):
                commit_body += f"* {path.relative_to(git_root)}\n"
            commit_body += "\n"

    return commit_body.strip()


def commit_all_changes(
    *,
    cd_path: Path = Path("."),
    message: str,
    scope: Path | None = None,
    include_body: bool = True,
    unstage: set[Path] = set(),
    enabled: bool = True,
) -> None:
    """Add all changed files to the staging area and commit with the specified commit message.

    Args:
        cd_path (Path): The path to run the git command from.
        message (str): The commit message.
        scope (Path | None): The path to add to the staging area. If None, all changes are added.
        include_body (bool): If True, include a generated commit body with the changes in the commit message.
        unstage (set[Path]): Tracked paths whose changes are kept out of this commit; they stay tracked
            at their committed version, with working-tree changes preserved on disk.
        enabled (bool): When False, skip all git interaction and return without committing.

    Raises:
        GitRepositoryError: If the command fails.

    """

    if not enabled:
        console.debug("Skipping git commits (git interactions disabled).")
        return

    git_root = locate_git_root(cd_path=cd_path)

    # Set scope to repo root if not provided
    scope = scope if scope is not None else locate_repo_root(cd_path=cd_path)

    git("reset", cd_path=git_root, expected_exit_code=0)
    git("add", "--", str(scope), cd_path=git_root, expected_exit_code=0)

    # Unstage these paths after the add so their changes are not committed; they stay tracked and on disk.
    for path in unstage:
        git("restore", "--staged", "--", str(path), cd_path=git_root, expected_exit_code=0)

    # git diff --exit-code returns 1 when there are staged changes and 0 when there are none;
    # both are valid outcomes, so branch on the returned code rather than treating 0 as a failure.
    result = git("diff", "--shortstat", "--staged", "--exit-code", cd_path=cd_path, expected_exit_code={0, 1})
    if result.returncode == 0:
        console.info("No changes to commit.")
        return
    stats = result.stdout.strip()

    if include_body:
        message += "\n\n" + generate_commit_body(cd_path, stage=True)

    git("commit", "-m", message, cd_path=cd_path, expected_exit_code=0)
    console.info(f"Changes committed. {stats}")
