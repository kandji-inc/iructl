"""Resolve the custom-app payload directory and keep its binaries out of git history."""

import logging
from pathlib import Path

from iructl import _git
from iructl._console import OutputConsole
from iructl._constants import PAYLOADS_DIR

console = OutputConsole(logging.getLogger(__name__))

# Written into payload_dir/.gitignore to exclude everything under it, including the file itself.
_GITIGNORE_CONTENTS = "*\n"


def resolve_payload_dir(repo_root: Path, override: str | None = None) -> Path:
    """Resolve the payload dir: <repo_root>/payloads, or override (relative paths anchor to repo_root)."""
    if override is None or not override.strip():
        return (repo_root / PAYLOADS_DIR).resolve()
    path = Path(override).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _in_git_tree(payload_dir: Path, repo_root: Path) -> Path | None:
    """Return the git root if payload_dir is inside the repo's git tree, else None."""
    git_root = _git.locate_git_root(cd_path=repo_root)
    return git_root if payload_dir.resolve().is_relative_to(git_root) else None


def tracked_payload_paths(payload_dir: Path, repo_root: Path) -> set[Path]:
    """Return the binaries under payload_dir that git is tracking (its .gitignore excluded)."""
    git_root = _in_git_tree(payload_dir, repo_root)
    if git_root is None:
        return set()
    result = _git.git("ls-files", "-z", "--", str(payload_dir), cd_path=git_root, expected_exit_code=0)
    return {git_root / entry for entry in result.stdout.split("\0") if entry and Path(entry).name != ".gitignore"}


def ensure_payloads_gitignore(payload_dir: Path, repo_root: Path) -> None:
    """Create payload_dir/.gitignore if missing and inside the git tree, so nothing under it is tracked."""
    git_root = _in_git_tree(payload_dir, repo_root)
    if git_root is None:
        return
    gitignore = payload_dir / ".gitignore"
    if gitignore.is_file():
        return

    payload_dir.mkdir(parents=True, exist_ok=True)
    gitignore.write_text(_GITIGNORE_CONTENTS, encoding="utf-8")
    console.log(logging.INFO, f"Created {gitignore}")


def warn_tracked_payloads(payload_dir: Path, repo_root: Path) -> None:
    """Warn when installer binaries are tracked in git, without untracking them."""
    tracked = tracked_payload_paths(payload_dir, repo_root)
    if not tracked:
        return
    listing = "\n".join(f"  - {path}" for path in sorted(tracked))
    console.print_warning(
        f"Installer binaries are tracked in git:\n{listing}\n"
        "They are left as-is and their changes are not committed. "
        "Untrack them with 'git rm --cached' if keeping them in version control was unintended."
    )


def protect_payload_dir(payload_dir: Path, repo_root: Path) -> None:
    """Ensure the payload .gitignore exists and warn about any already-tracked binaries."""
    ensure_payloads_gitignore(payload_dir, repo_root)
    warn_tracked_payloads(payload_dir, repo_root)
