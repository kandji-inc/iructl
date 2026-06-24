import functools
import hashlib
import logging
import re
import shlex
import unicodedata
from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml import YAML

from iructl._console import OutputConsole
from iructl._constants import APP_BRANDING, APP_NAME, LEGACY_ROOT_MARKER, ROOT_MARKER
from iructl.exceptions import InvalidRepositoryError, UnmigratedRepositoryError

console = OutputConsole(logging.getLogger(__name__))

yaml = YAML()
yaml.indent(mapping=2, sequence=4, offset=2)

_HASH_CHUNK_SIZE = 1024 * 1024


def validation_error_messages(error: ValidationError) -> list[str]:
    """Return each error as ``<field path>: <message>``.

    The field path tells the user which key failed (pydantic omits it from the
    message itself), and the ``Value error,`` prefix it prepends to messages
    raised by custom validators is stripped. Model-level errors carry no
    location and are returned as the bare message.
    """
    messages = []
    for detail in error.errors():
        message = detail["msg"].removeprefix("Value error,").strip()
        location = ".".join(str(part) for part in detail["loc"])
        messages.append(f"{location}: {message}" if location else message)
    return messages


def sha256_file(path: Path) -> str:
    """Return the sha256 of a file, read in chunks to avoid loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nearest_existing_dir(cd_path: Path) -> Path:
    """Resolve cd_path to its nearest existing ancestor directory.

    Both root-finders walk up from a (possibly non-existent) path, so the upward search must start
    from a directory that actually exists on disk.

    Args:
        cd_path (Path): The path to resolve. Need not exist.

    Returns:
        Path: The nearest existing ancestor directory of the resolved cd_path.

    Raises:
        InvalidRepositoryError: If no existing parent directory can be found.

    """

    cd_path = cd_path.expanduser().resolve()
    existing_dir = next((p for p in (cd_path, *cd_path.parents) if p.is_dir()), None)
    if existing_dir is None or existing_dir == Path(existing_dir.anchor):
        msg = f"Failed to locate an existing parent directory for {cd_path}"
        raise InvalidRepositoryError(msg)
    return existing_dir


def _unmigrated_kst_migration(ancestors: list[Path]) -> UnmigratedRepositoryError | None:
    """Return an UnmigratedRepositoryError when a legacy ``.kst`` marker lacks the current marker."""

    def _shorter_display(target: Path) -> Path:
        """Return target relative to the cwd or absolute, whichever renders shorter."""

        try:
            relative = target.relative_to(Path.cwd(), walk_up=True)
        except ValueError:
            # relative_to raises when target and cwd are on different drives (Windows).
            return target
        return relative if len(str(relative)) <= len(str(target)) else target

    if ROOT_MARKER == LEGACY_ROOT_MARKER:
        # Running under the legacy kst branding; the marker is already current.
        return None
    legacy_root = next((p for p in ancestors if (p / LEGACY_ROOT_MARKER).is_file()), None)
    if legacy_root is None:
        return None
    root = _shorter_display(legacy_root)
    warning = (
        f"Found a `{LEGACY_ROOT_MARKER}` file but no `{ROOT_MARKER}` file.\n"
        "This looks like an unmigrated kst repository."
    )
    command = f"mv {shlex.quote(str(root / LEGACY_ROOT_MARKER))} {shlex.quote(str(root / ROOT_MARKER))}"
    return UnmigratedRepositoryError(warning, command)


@functools.cache
def locate_repo_root(*, cd_path: Path = Path(".")) -> Path:
    """Locate the iructl repository root: the nearest ancestor containing the marker file.

    The result is cached for repeated lookups of the same path.

    Args:
        cd_path (Path): The path to start the upward search from.

    Returns:
        Path: The iructl repository root (the directory holding the ``ROOT_MARKER`` file).

    Raises:
        UnmigratedRepositoryError: If only a legacy ``.kst`` marker is found at or above cd_path.
        InvalidRepositoryError: If no repository marker is found at or above cd_path.

    """

    existing_dir = nearest_existing_dir(cd_path)
    ancestors: list[Path] = []
    repo_root = None
    for parent in (existing_dir, *existing_dir.parents):
        ancestors.append(parent)
        if (parent / ROOT_MARKER).is_file():
            repo_root = parent
            break
        if (parent / ".git").exists():
            break  # bound the search at the enclosing git working tree (filesystem check; no git needed)

    if repo_root is not None:
        console.debug(f"Located {APP_NAME} repository root at {repo_root}")
        return repo_root

    if unmigrated := _unmigrated_kst_migration(ancestors):
        raise unmigrated

    msg = (
        f"The directory does not appear to be a {APP_BRANDING} repository. If it should be, "
        f'please make sure a "{ROOT_MARKER}" file exists in the repository.'
    )
    raise InvalidRepositoryError(msg)


def sanitize_filename(value: str) -> str:
    replacement = "_"
    max_length = 255

    # https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file#naming-conventions
    # Linux/macOS don't allow / or \0
    invalid_chars_pattern = re.compile(r'[<>:"/\\|?*\x00-\x1F]')

    # https://docs.python.org/3/library/unicodedata.html#unicodedata.normalize
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")

    # Replace invalid characters with the replacement character then collapse
    value = re.sub(invalid_chars_pattern, replacement, value)
    value = re.sub(f"{replacement}+", replacement, value)

    # Strip leading/trailing replacement characters and spaces
    # Strip trailing periods (for Windows)
    value = value.lstrip(" ")
    value = value.rstrip(". ")

    if not value:
        return replacement

    return value[:max_length]


def content_suffixed_filename(name: str, sha256: str) -> str:
    """Append the local content-hash suffix (``_<sha8>``) to an installer name, idempotently."""
    path = Path(name)
    suffix = f"_{sha256[:8]}"
    return name if path.stem.endswith(suffix) else f"{path.stem}{suffix}{path.suffix}"
