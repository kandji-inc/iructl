from __future__ import annotations

import contextlib
import io
import json
import logging
import plistlib
import shutil
import threading
import traceback
from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import urljoin
from uuid import UUID

import requests
import typer
from pydantic import ValidationError
from rich import box
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from iructl._cli.common import (
    ActionResponse,
    ActionType,
    BlueprintActionOutcome,
    ForceMode,
    OperationType,
    PayloadTransfer,
    PreparedAction,
    ResultType,
    SyncResults,
)
from iructl._console import OutputConsole, OutputFormat, SyntaxType, render_plain_text
from iructl._constants import APP_BRANDING, APP_NAME, REPORT_FILE, SOURCE, TENANT_ENV, TOKEN_ENV
from iructl._diff import ChangesDict, ChangeType, three_way_diff
from iructl._progress import NULL_REPORTER, UPLOAD_FRACTION, BatchReporter, StreamReporter, batch_progress
from iructl._utils import locate_repo_root, validation_error_messages, yaml
from iructl.api import ApiConfig, BlueprintsResource, is_duplicate_assignment
from iructl.exceptions import (
    InvalidRepositoryError,
    InvalidRepositoryMemberError,
    PayloadTransferError,
    UnmigratedRepositoryError,
)
from iructl.repository import (
    ACCEPTED_INFO_EXTENSIONS,
    SUFFIX_MAP,
    InfoFormat,
    MemberBase,
    Repository,
    RepositoryDirectory,
)
from iructl.repository.blueprints import get_blueprint_id, get_blueprint_name
from iructl.repository.custom_app import CustomApp, DownloadResult

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

console = OutputConsole(logging.getLogger(__name__))

_PING_TIMEOUT = (10, 30)  # (connect timeout, read timeout)


def _warn_unmigrated_repo(error: UnmigratedRepositoryError) -> None:
    """Render the migration warning panel, then the rename command as plain text."""
    console.print_warning(
        Panel(
            error.warning, title="Unmigrated kst repository", title_align="left", border_style="warning", expand=False
        )
    )
    console.print_warning("To migrate, rename the marker file:")
    console.print_warning(f"  {escape(error.command)}")


# --- Utility functions ---
def api_config_prompt(tenant_url: str | None, api_token: str | None, interactive: bool = True) -> ApiConfig:
    """Prompt the user for missing API configuration values unless interactive is False.

    The function will prompt the user for the tenant_url and api_token if they are not
    provided as arguments. In the event that the function cannot return a valid ApiConfig
    it will raise a typer. Exit exception with a status code of 2.

    Args:
        tenant_url (str | None): The Iru Tenant URL.
        api_token (str | None): The Iru API Token.
        interactive (bool): Whether to prompt the user for missing values.

    Returns:
        ApiConfig: A validated ApiConfig object.

    Raises:
        typer.BadParameter: If the function cannot return a valid ApiConfig object.

    """

    if interactive and tenant_url is None:
        console.debug("Tenant URL not provided. Prompting for input.")
        tenant_url = typer.prompt("Enter Iru Tenant API URL")
    if interactive and api_token is None:
        console.debug("API Token not provided. Prompting for input.")
        api_token = typer.prompt("Enter API Token", hide_input=True)

    if tenant_url is None:
        msg = f"You must provide a valid Iru Tenant API URL. Use the --tenant-url flag or the set the {TENANT_ENV} environment variable."
        console.error(msg)
        raise typer.BadParameter(msg)
    if api_token is None:
        msg = f"You must provide a valid Iru API Token. Use the --api-token flag or the set the {TOKEN_ENV} environment variable."
        console.error(msg)
        raise typer.BadParameter(msg)

    try:
        console.debug(f"Creating ApiConfig with tenant_url: {tenant_url}")
        config = ApiConfig(tenant_url=tenant_url, api_token=api_token)
    except ValidationError as error:
        msg = "\n* " + "\n* ".join(validation_error_messages(error))
        console.error(msg)
        raise typer.BadParameter(msg)

    # Ensure the URL is a valid Iru tenant API URL
    console.debug(f"Validating URL: {config.url}")
    try:
        response = requests.get(urljoin(config.url, "/app/v1/ping"), params={"source": SOURCE}, timeout=_PING_TIMEOUT)
    except requests.RequestException as error:
        msg = _http_error_detail(error, config.url)
        console.error(msg)
        raise typer.BadParameter(msg)
    if not response.ok:
        msg = f"Unable to connect to ({config.url}). Please check the URL then try again."
        console.error(msg)
        raise typer.BadParameter(msg)

    console.debug(f"Response content: {response.text}")
    console.debug(f"Response status code: {response.status_code}")

    return config


def delete_member_directory(member: MemberBase):
    """Delete a member directory from the local repository.

    Args:
        member: The repository member to delete.

    """
    if member.info_path.parent.exists():
        console.debug(f"Deleting directory for {member.id} at {member.info_path.parent}")
        shutil.rmtree(member.info_path.parent)
        if member.info_path.parent.exists():
            console.print_error(
                f"The directory for {member.id} was not deleted. Please cleanup manually ({member.info_path.parent})."
            )
        else:
            console.debug(f"Directory for {member.id} deleted successfully.")
            for child in member.children:
                child.path = None
    else:
        console.print_warning(
            f"The directory for {member.id} was not found on disk at {member.info_path.parent}. It may have already been deleted."
        )


def filter_changes[MemberType: MemberBase](
    local_repo: Repository[MemberType], remote_repo: Repository[MemberType]
) -> ChangesDict:
    """Compare two repositories and return the changes between them.

    Args:
        local_repo (Repository): The local repository.
        remote_repo (Repository): The remote repository.

    Returns:
        ChangesDict: A dictionary of changes between the repositories.

    """
    changes: ChangesDict[MemberType] = {
        ChangeType.NONE: [],
        ChangeType.CREATE_REMOTE: [],
        ChangeType.UPDATE_REMOTE: [],
        ChangeType.CREATE_LOCAL: [],
        ChangeType.UPDATE_LOCAL: [],
        ChangeType.CONFLICT: [],
    }
    all_ids = set(local_repo.keys()) | set(remote_repo.keys())
    for member_id in all_ids:
        local_member = local_repo.get(member_id)
        remote_member = remote_repo.get(member_id)

        base_hash = local_member.sync_hash if local_member is not None else None
        local_hash = local_member.diff_hash if local_member is not None else None
        remote_hash = remote_member.diff_hash if remote_member is not None else None

        change_type = three_way_diff(
            base=base_hash,
            local=local_hash,
            remote=remote_hash,
        )
        console.debug(f"Change type for {member_id}: {change_type}")

        changes[change_type].append((local_member, remote_member))
    return changes


def is_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    with contextlib.suppress(ValueError):
        UUID(value, version=4)
        console.debug(f"Value {value} is a valid UUID.")
        return True
    console.debug(f"Value {value} is not a valid UUID.")
    return False


def update_local_member[MemberType: MemberBase](
    local_repo: Repository[MemberType], result: ActionResponse[MemberType]
) -> None:
    """Update local repository with the api response from the sync operation.

    Args:
        local_repo (Repository): The local repository object.
        result (ActionResponse): The results of the sync operation.

    """
    if local_repo.root is None:
        raise ValueError("The local_repo must have a root path set. Got None.")

    if result.member is None:
        raise ValueError(f"The result's member attribute must not be None (got {result}).")

    if result.id not in local_repo:
        raise ValueError(f"The result's ID must be in the local repository (got {result.id}).")

    # Merge the API response into the local member and stamp its sync hash
    local_member = type(result.member).synced(local_repo[result.id], result.member)

    # If the ID has changed delete the old ID before adding the updated ID
    if result.id != result.member.id:
        del local_repo[result.id]

    # Write the repository member to disk
    local_member.ensure_paths(local_repo.root)
    local_member.write()

    # Add the updated member to the local repository
    local_repo[result.member.id] = local_member


def validate_output_path(
    *, directory: RepositoryDirectory, override: str | None = None, repo: str | Path = "."
) -> Path:
    """Return the output path for the new repository member.

    If an override path is provided, it will be used. Otherwise, the repository at ``repo`` (the
    top-level --repo path, defaulting to the current working directory) will be used.

    If an invalid override path is provided or the repository at ``repo`` cannot be located, an
    exception will be raised.

    Args:
        directory (RepositoryDirectory): The member subdirectory to resolve within the repository.
        override (str | None): An explicit output path that overrides the repository default.
        repo (str | Path): The repository directory to default into when no override is given.

    Returns:
        Path: The output path.

    Raises:
        typer.BadParameter: If the output path cannot be determined

    """

    # If an override path is provided, check that it is a valid path in an iructl repository
    if override is not None:
        override_path = Path(override).expanduser().resolve()
        console.debug(f"Output path provided: {override_path}")
        try:
            member_root = locate_repo_root(cd_path=override_path) / directory
            if not override_path.is_relative_to(member_root):
                raise InvalidRepositoryError
        except UnmigratedRepositoryError as error:
            _warn_unmigrated_repo(error)
            raise typer.Exit(code=1)
        except InvalidRepositoryError:
            msg = (
                f"The output path must be located inside a {directory} directory of a valid {APP_BRANDING} repository."
            )
            console.error(msg)
            raise typer.BadParameter(msg)
        return override_path.resolve()

    # If no override is provided, default into the repo directory (--repo, or cwd when unset).
    repo_path = Path(repo).expanduser().resolve()
    try:
        console.debug(f"No output path provided. Using repository at {repo_path}.")
        return locate_repo_root(cd_path=repo_path) / directory
    except UnmigratedRepositoryError as error:
        _warn_unmigrated_repo(error)
        raise typer.Exit(code=1)
    except InvalidRepositoryError:
        msg = (
            f"An output path was not specified and {repo_path} is not an initialized {APP_BRANDING} repository. "
            "Run from inside a repository, pass --repo, or use -o/--output with a path inside a valid repository."
        )
        console.error(msg)
        raise typer.BadParameter(msg)


def finalize_new_member(member: MemberBase, output_path: Path, *, sources: dict[str, Path], copy_mode: bool) -> None:
    """Place a freshly built member on disk: write info + generated children, copy/move imported sources.

    sources maps a content-child attribute to the file it was imported from. Children absent from
    sources were generated (their default content is written), and absent children are skipped.
    """
    member.ensure_paths(output_path)
    for spec in member._config.content_specs:
        child = getattr(member, spec.attribute)
        source = sources.get(spec.attribute)
        if child is None or source is None:
            continue
        candidate = f"{spec.attribute}{source.suffix}"
        child.path = child.path.with_name(candidate if fnmatch(candidate, spec.glob) else spec.filename)

    member.write(write_content=False)  # info only; children are placed below

    for spec in member._config.content_specs:
        child = getattr(member, spec.attribute)
        if child is None:
            continue
        source = sources.get(spec.attribute)
        if source is None:
            child.write()
        else:
            (shutil.copy if copy_mode else shutil.move)(source, child.path)


def run_show[MemberType: MemberBase](
    member_type: type[MemberType],
    key: str,
    *,
    remote: bool,
    repo_str: str,
    tenant_url: str | None,
    api_token: str | None,
    format: OutputFormat,
    output: str,
    project: Callable[[MemberType, OutputFormat], tuple[str | None, SyntaxType | None]],
) -> None:
    """Fetch one member and emit it.

    project returns (plain_output, syntax) for the requested view; a plain_output of None means
    render the member's table to stdout (or its plain text when writing to a file).
    """
    config = api_config_prompt(tenant_url, api_token) if remote else None
    member = get_member(config=config, member_type=member_type, key=key, repo=repo_str, remote=remote)
    plain_output, syntax = project(member, format)

    if output == "-":
        if plain_output is not None:
            console.print_syntax(plain_output, syntax=syntax)
        else:
            console.print(member.format_table())
    else:
        output_path = Path(output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            plain_output if plain_output is not None else member.format_plain_text(format), encoding="utf-8"
        )


def validate_repo_path(
    repo: Path | str = ".", subdir: RepositoryDirectory | None = None, validate_subdir: bool = False
) -> Path:
    """Validate and return a valid iructl repository path."""

    repo_path = Path(repo).expanduser().resolve()
    try:
        root = locate_repo_root(cd_path=repo_path)
        if subdir is not None:
            if validate_subdir and not repo_path.is_relative_to(root / subdir):
                raise InvalidRepositoryError
            (root / subdir).mkdir(parents=True, exist_ok=True)
            return root / subdir
        return root
    except UnmigratedRepositoryError as error:
        _warn_unmigrated_repo(error)
        raise typer.Exit(code=1)
    except InvalidRepositoryError:
        subdir_name = subdir + " " if subdir is not None and validate_subdir else ""
        msg = f"The path provided for --repo option is not a valid {APP_BRANDING} {subdir_name}directory. (got {repo_path})"
        console.error(msg)
        raise typer.BadParameter(msg)


def verify_all_ids_found[MemberType: MemberBase](
    member_ids: Iterable[str],
    local_repo: Repository[MemberType],
    remote_repo: Repository[MemberType],
    local_only=False,
    remote_only=False,
) -> None:
    """Verify that all member IDs are found in either the local or remote repository.

    Args:
        member_ids (Iterable[str]): The IDs of the members to verify.
        local_repo (Repository): The local repository to check.
        remote_repo (Repository): The remote repository to check.

    Raises:
        typer.BadParameter: If a member ID is not found in the local or remote repositories.

    """
    errors = []
    for member_id in member_ids:
        if member_id not in local_repo and member_id not in remote_repo:
            location = "local" if local_only else "remote" if remote_only else "local or remote"
            errors.append(f"Repository member with ID {member_id} not found in {location} repository.")

    if errors:
        console.error("\n".join(errors))
        raise typer.BadParameter("\n".join(errors))


# --- Load / Fetch Functions ---
def load_members_by_id[MemberType: MemberBase](
    repo_path: Path, member_type: type[MemberType], member_ids: Iterable[str], raise_on_missing=True
) -> Iterator[MemberType]:
    """Load repository members by their IDs.

    Args:
        repo_path: The path to the repository.
        member_type: The repository member class.
        member_ids: The IDs of the members to load.
        raise_on_missing: Raise an error if a member ID is not found.

    Yields:
        The repository members with the given IDs.

    Raises:
        typer.Exit: If the given ID is not found and raise_on_missing is True.
        typer.BadParameter: If the given ID is not found and raise_on_missing is True.

    """
    if not member_ids:
        return

    try:
        repo = Repository.load_path(model=member_type, path=repo_path)
        console.debug(f"Loaded repository at {repo.root}")
    except InvalidRepositoryError as error:
        console.print_error(f"An error occurred while loading the repository: {error}")
        raise typer.Exit(code=1)
    except InvalidRepositoryMemberError as error:
        console.print_error(f"An error occurred while loading a repository member: {error}")
        raise typer.Exit(code=1)

    for member_id in member_ids:
        try:
            console.debug(f"Retrieving member with ID {member_id} from local repository.")
            yield repo[member_id]
        except KeyError:
            if not raise_on_missing:
                console.debug(f"Skipping missing member with ID {member_id}")
                continue
            msg = f"Member with ID {member_id} not found in local repository at {repo_path.resolve()}."
            console.error(msg)
            raise typer.BadParameter(msg)


def load_members_by_path[MemberType: MemberBase](
    member_type: type[MemberType], member_paths: Iterable[Path], raise_on_missing=True
) -> Iterator[MemberType]:
    """Load repository members from a list of paths.

    Args:
        member_type (type[RepositoryMemberType]): The type of the repository member.
        member_paths (Iterable[Path]): The paths to the repository members.
        raise_on_missing (bool): Raise an error if a member path is not found.

    Yields:
        Repository members loaded from the given paths.

    Raises:
        typer.Exit: If a repository member cannot be loaded from a given path.
        typer.BadParameter: If a given path does not exist and raise_on_missing is True.

    """
    if not member_paths:
        return

    for member_path in member_paths:
        # Throw error if trying to load the wrong type from a path.
        validate_repo_path(
            repo=member_path, subdir=RepositoryDirectory(member_type.directory_name), validate_subdir=True
        )

    path_set = set()
    for path in member_paths:
        console.debug(f"Loading member from path: {path}")
        if not path.exists():
            if not raise_on_missing:
                console.debug(f"Skipping missing path {path}")
                continue
            msg = f"Path {path} does not exist."
            console.error(msg)
            raise typer.BadParameter(msg)
        if path.is_dir():
            console.debug(f"Path {path} is a directory. Loading recursively.")
            yield from load_members_by_path(
                member_type=member_type,
                member_paths=[p for p in path.rglob("info.*") if p.suffix in ACCEPTED_INFO_EXTENSIONS],
            )
        else:
            if path in path_set:
                console.debug(f"Skipping duplicate path {path}")
                continue
            try:
                yield member_type.from_path(path)
            except InvalidRepositoryMemberError as error:
                console.print_error(f"An error occurred while loading {path}: {error}")
                raise typer.Exit(code=1)


def get_local_members[MemberType: MemberBase](
    repo: Path,
    member_type: type[MemberType],
    *,
    member_paths: Iterable[Path] = [],
    member_ids: Iterable[str] = [],
    all_members: bool = False,
    raise_on_missing_id: bool = True,
    raise_on_missing_path: bool = True,
) -> Repository[MemberType]:
    """Get a filtered and de-duplicated local repository object.

    Args:
        repo (Path): The path to the repository.
        member_paths (Iterable[Path]): The paths to the repository members to include.
        member_ids (Iterable[str]): The IDs of the repository members to include.
        all_members (bool): Include all members. If True, paths and member_ids are ignored.
        raise_on_missing_id (bool): Raise an error if a member ID is not found.
        raise_on_missing_path (bool): Raise an error if a member path is not found.

    Returns:
        The generated repository mapping.

    Raises:
        typer.Exit: If selected repository members cannot be loaded.
        typer.BadParameter: If not all members are found in the repository.

    """

    member_id_set = set(member_ids)

    if all_members:
        try:
            console.debug(f"Loading all members from the repository at {repo}")
            return Repository.load_path(model=member_type, path=repo)
        except InvalidRepositoryError as error:
            console.print_error(f"An error occurred while loading the repository: {error}")
            raise typer.Exit(code=1)
        except InvalidRepositoryMemberError as error:
            console.print_error(f"An error occurred while loading a repository member: {error}")
            raise typer.Exit(code=1)

    # Gather list of items to push and remove duplicates
    members = list(
        load_members_by_id(
            repo_path=repo, member_type=member_type, member_ids=member_id_set, raise_on_missing=raise_on_missing_id
        )
    )
    members.extend(
        member
        for member in load_members_by_path(
            member_type=member_type, member_paths=set(member_paths), raise_on_missing=raise_on_missing_path
        )
        if member.id not in member_id_set
    )
    console.debug(f"Loaded {len(members)} members from the repository at {repo}")

    if not all(member.info_path.parent.is_relative_to(repo.resolve()) for member in members):
        msg = f"All repository members must be within the repository at {repo}."
        console.error(msg)
        raise typer.BadParameter(msg)

    try:
        console.debug("Generating repository mapping for loaded local members.")
        return Repository(members, root=repo)
    except InvalidRepositoryError as error:
        console.print_error(f"An error occurred while loading the repository: {error}")
        raise typer.Exit(code=1)


class InvalidRemoteMember(NamedTuple):
    """A remote member whose API payload could not be converted to a member model."""

    id: str
    name: str
    error: str


class RemoteMembers[MemberType: MemberBase](NamedTuple):
    """A remote repository mapping plus the members that failed conversion."""

    repo: Repository[MemberType]
    invalid: list[InvalidRemoteMember]


class FilteredMembers[MemberType: MemberBase](NamedTuple):
    """A local repository mapping with invalid remote members removed, plus their IDs."""

    repo: Repository[MemberType]
    invalid_ids: set[str]


def record_invalid_members[MemberType: MemberBase](
    results: SyncResults[MemberType], invalid: list[InvalidRemoteMember]
) -> None:
    """Append synthesized failure entries for remote members that failed conversion."""
    results.failure.extend(
        ActionResponse(
            id=member.id,
            action=ActionType.INVALID,
            operation=OperationType.SKIP,
            result=ResultType.FAILURE,
            member=None,
            reason=member.error,
        )
        for member in invalid
    )


def count_invalid_failures[MemberType: MemberBase](results: SyncResults[MemberType]) -> int:
    """Count the synthesized invalid-remote-member entries in the failure bucket."""
    return sum(1 for failure in results.failure if failure.action is ActionType.INVALID)


def filter_invalid_members[MemberType: MemberBase](
    local_repo: Repository[MemberType], invalid: list[InvalidRemoteMember]
) -> FilteredMembers[MemberType]:
    """Return a copy of local_repo without the invalid remote members, plus their IDs.

    An invalid member absent from the remote mapping but present locally would be
    misclassified as locally new by the three-way diff (duplicate create on push, local
    delete on pull --clean), so its local counterpart must be excluded from action
    planning entirely.
    """
    invalid_ids = {member.id for member in invalid}
    filtered = Repository(
        (member for member_id, member in local_repo.items() if member_id not in invalid_ids),
        root=local_repo.root,
    )
    return FilteredMembers(filtered, invalid_ids)


def get_remote_members[MemberType: MemberBase](
    config: ApiConfig,
    member_type: type[MemberType],
    *,
    member_ids: Iterable[str] = [],
    all_members: bool = False,
    raise_on_missing: bool = True,
) -> RemoteMembers[MemberType]:
    """Get a filtered object of remote repository members.

    A member whose payload fails conversion does not abort the fetch: it is warned
    about and returned in the invalid list so callers can exclude its ID from action
    planning (an omitted member would be misclassified as locally new).

    Args:
        config (ApiConfig): The API configuration.
        member_type (RepositoryMemberType): The type of members to fetch.
        member_ids (Iterable[str]): The IDs of the members to include.
        all_members (bool): Include all members. If True, member_ids is ignored.
        raise_on_missing (bool): Raise an error if a repository member is not found.

    Returns:
        The generated repository mapping and the members that failed conversion.

    Raises:
        typer.Exit: If an error occurs while fetching members.
        typer.BadParameter: If not all members are found in the repository.

    """

    try:
        console.debug(f"Fetching members from the remote API at {config.url}")
        members = member_type.list_remote(config=config).results
        console.debug(f"Fetched {len(members)} members")
    except (requests.RequestException, ValidationError) as error:
        console.print_error(f"An error occurred while fetching: {_http_error_detail(error, config.url)}")
        raise typer.Exit(code=1)

    if not all_members:
        console.debug(f"Filtering members to requested IDs: {member_ids}")
        members = [member for member in members if member.id in member_ids]

    missing_ids = set(member_ids) - {member.id for member in members}
    for member_id in missing_ids:
        console.debug(f"Member with ID {member_id} not found in Iru.")

    if raise_on_missing and missing_ids:
        msg = "Requested members not found in Iru:"
        for member_id in missing_ids:
            msg += f"\n* {member_id}"
        console.error(msg)
        raise typer.BadParameter(msg)

    console.debug("Generating repository mapping for fetched remote members.")
    converted: list[MemberType] = []
    invalid: list[InvalidRemoteMember] = []
    for member in members:
        try:
            converted.append(member_type.from_api_payload(member))
        except (ValidationError, InvalidRepositoryMemberError) as error:
            detail = "; ".join(validation_error_messages(error)) if isinstance(error, ValidationError) else str(error)
            console.print_warning(f"Skipping invalid remote member '{member.name}' ({member.id}): {detail}")
            invalid.append(InvalidRemoteMember(id=member.id, name=member.name, error=detail))
    return RemoteMembers(Repository[member_type](converted), invalid)


def get_member[MemberType: MemberBase](
    config: ApiConfig | None, member_type: type[MemberType], key: str, repo: str, remote: bool
) -> MemberType:
    """Get a repository member by ID or path from a local repo or remote API

    Args:
        config: The API configuration to use if fetching from remote.
        repo: The path to the local repository.
        key: The ID or path to search.
        remote: If True, search the remote repo. Otherwise, search the local repo.

    Returns:
        The repository member object.

    Raises:
        typer.BadParameter: If the input is not a valid ID or path.

    """
    if is_uuid(key):
        console.debug(f"Key {key} identified as ID. Loading from ID.")
        if remote:
            member_id = key
        else:
            return next(
                load_members_by_id(
                    repo_path=validate_repo_path(repo=repo, subdir=RepositoryDirectory(member_type.directory_name)),
                    member_type=member_type,
                    member_ids=[key],
                )
            )
    elif Path(key).exists():
        console.debug(f"Key {key} identified as path. Loading from path.")
        discovered_members = list(load_members_by_path(member_type=member_type, member_paths=[Path(key)]))
        if len(discovered_members) == 1:
            if remote:
                member_id = discovered_members[0].id
            else:
                return discovered_members[0]
        elif len(discovered_members) == 0:
            msg = f"Found no items at path {key}. Please check the path and try again."
            console.error(msg)
            raise typer.BadParameter(msg)
        else:
            msg = f"Found {len(discovered_members)} items at path {Path(key).expanduser().resolve()}."
            for member in discovered_members:
                msg += f"\n* {member.id}"
            msg += "\nPlease specify an ID or direct path."
            console.error(msg)
            raise typer.BadParameter(msg)
    else:
        msg = f"{key} is not a valid ID or existing path. Please double-check the lookup value."
        console.error(msg)
        raise typer.BadParameter(msg)

    # config is guaranteed to be non-None if remote is True
    remote_members = get_remote_members(
        config=config,  # type: ignore[reportArgumentType]
        member_type=member_type,
        member_ids=[member_id],
        raise_on_missing=True,
    )
    if remote_members.invalid:
        bad = remote_members.invalid[0]
        console.print_error(f"The remote member '{bad.name}' ({bad.id}) could not be loaded: {bad.error}")
        raise typer.Exit(code=1)
    return next(iter(remote_members.repo.values()))


# --- Prepare Action Functions ---
def _pull_download_intent(
    member: MemberBase, payload_dir: Path | None, *, download: bool, force: bool
) -> DownloadResult | None:
    """The planned installer transfer for a pulled member, or None when downloads aren't requested."""
    if not download or payload_dir is None:
        return None
    return member.plan_download(payload_dir, force=force)


def prepare_pull_actions[MemberType: MemberBase](
    changes: ChangesDict[MemberType],
    force_pull: bool = False,
    allow_delete: bool = False,
    *,
    payload_dir: Path | None = None,
    download: bool = False,
) -> list[PreparedAction[MemberType]]:
    """Prepare an iterable of actionable items from the changes dictionary.

    Args:
        changes (ChangesDict): The changes dictionary to prepare actions from.
        force_pull (bool): Whether to force pull changes in case of conflicts.
        allow_delete (bool): Whether to allow deletion.
        payload_dir (Path | None): Where installer binaries live, when download is requested.
        download (bool): Whether to also ensure each pulled app's installer is present locally.

    Returns:
        list[PreparedAction]: A list of actions to take for each repository member. With download
        on, pull actions carry a transfer intent and in-sync apps needing a binary become TRANSFER
        actions.

    """

    def intent(member: MemberType) -> DownloadResult | None:
        return _pull_download_intent(member, payload_dir, download=download, force=force_pull)

    actions = []
    for change_type, members in changes.items():
        match change_type:
            case ChangeType.CREATE_REMOTE:
                actions.extend(
                    PreparedAction(
                        action=ActionType.CREATE,
                        operation=OperationType.PULL,
                        change=change_type,
                        member=remote,
                        other=local,
                        download=intent(remote),
                    )
                    for local, remote in members
                    if remote is not None
                )
            case ChangeType.UPDATE_REMOTE:
                actions.extend(
                    PreparedAction(
                        action=ActionType.UPDATE,
                        operation=OperationType.PULL,
                        change=change_type,
                        member=remote,
                        other=local,
                        download=intent(remote),
                    )
                    for local, remote in members
                    if remote is not None
                )
            case ChangeType.NONE:
                # Metadata is in sync, but the local installer may be absent/stale -- a TRANSFER.
                actions.extend(
                    PreparedAction(
                        action=ActionType.TRANSFER,
                        operation=OperationType.PULL,
                        change=change_type,
                        member=remote,
                        other=local,
                        download=planned,
                    )
                    for local, remote in members
                    if remote is not None
                    and (planned := intent(remote))
                    in {DownloadResult.DOWNLOADED, DownloadResult.MISMATCH_SKIPPED, DownloadResult.MIGRATED}
                )
            case ChangeType.CONFLICT | ChangeType.UPDATE_LOCAL:
                if force_pull:
                    actions.extend(
                        PreparedAction(
                            action=ActionType.UPDATE,
                            operation=OperationType.PULL,
                            change=change_type,
                            member=remote,
                            other=local,
                            download=intent(remote),
                        )
                        for local, remote in members
                        if remote is not None
                    )
                else:
                    actions.extend(
                        PreparedAction(
                            action=ActionType.SKIP,
                            operation=OperationType.SKIP,
                            change=change_type,
                            member=local,
                            other=remote,
                        )
                        for local, remote in members
                        if local is not None
                    )
            case ChangeType.CREATE_LOCAL:
                if allow_delete:
                    actions.extend(
                        PreparedAction(
                            action=ActionType.DELETE,
                            operation=OperationType.PULL,
                            change=change_type,
                            member=local,
                            other=remote,
                        )
                        for local, remote in members
                        if local is not None
                    )
                else:
                    actions.extend(
                        PreparedAction(
                            action=ActionType.SKIP,
                            operation=OperationType.SKIP,
                            change=change_type,
                            member=local,
                            other=remote,
                        )
                        for local, remote in members
                        if local is not None
                    )
    return actions


def prepare_push_actions[MemberType: MemberBase](
    changes: ChangesDict[MemberType], force_push: bool = False, allow_delete: bool = False
) -> list[PreparedAction[MemberType]]:
    """Prepare an iterable of actionable items from the changes dictionary.

    Args:
        changes (ChangesDict): The changes dictionary to prepare actions from.
        force_push (bool): Whether to force push changes in case of conflicts.
        allow_delete (bool): Whether to allow deletion of members.

    Returns:
        list[PreparedAction]: A list of actions to take for each repository member.

    """
    actions: list[PreparedAction[MemberType]] = []
    for change_type, members in changes.items():
        match change_type:
            case ChangeType.CREATE_LOCAL:
                actions.extend(
                    PreparedAction(
                        action=ActionType.CREATE,
                        operation=OperationType.PUSH,
                        change=change_type,
                        member=local,
                        other=remote,
                    )
                    for local, remote in members
                    if local is not None
                )
            case ChangeType.UPDATE_LOCAL:
                actions.extend(
                    PreparedAction(
                        action=ActionType.UPDATE,
                        operation=OperationType.PUSH,
                        change=change_type,
                        member=local,
                        other=remote,
                    )
                    for local, remote in members
                    if local is not None
                )
            case ChangeType.CONFLICT | ChangeType.UPDATE_REMOTE:
                if force_push:
                    actions.extend(
                        PreparedAction(
                            action=ActionType.UPDATE,
                            operation=OperationType.PUSH,
                            change=change_type,
                            member=local,
                            other=remote,
                        )
                        for local, remote in members
                        if local is not None
                    )
                else:
                    actions.extend(
                        PreparedAction(
                            action=ActionType.SKIP,
                            operation=OperationType.SKIP,
                            change=change_type,
                            member=remote,
                            other=local,
                        )
                        for local, remote in members
                        if remote is not None
                    )
            case ChangeType.CREATE_REMOTE:
                if allow_delete:
                    actions.extend(
                        PreparedAction(
                            action=ActionType.DELETE,
                            operation=OperationType.PUSH,
                            change=change_type,
                            member=remote,
                            other=local,
                        )
                        for local, remote in members
                        if remote is not None
                    )
                else:
                    actions.extend(
                        PreparedAction(
                            action=ActionType.SKIP,
                            operation=OperationType.SKIP,
                            change=change_type,
                            member=remote,
                            other=local,
                        )
                        for local, remote in members
                        if remote is not None
                    )
    return actions


def prepare_delete_actions[MemberType: MemberBase](
    local_repo: Repository[MemberType],
    remote_repo: Repository[MemberType],
    member_ids: Iterable[str],
    local_only: bool,
    remote_only: bool,
) -> list[PreparedAction[MemberType]]:
    """Prepare delete actions for repository members."""
    actions: list[PreparedAction] = []
    for member_id in member_ids:
        if not local_only:
            remote_member = remote_repo.get(member_id)
            if remote_member is not None:
                actions.append(
                    PreparedAction(
                        action=ActionType.DELETE,
                        operation=OperationType.PUSH,  # Push to signify remote deletion
                        change=ChangeType.CREATE_REMOTE,  # Create remote to signify missing remote member
                        member=remote_member,
                    )
                )
        if not remote_only:
            local_member = local_repo.get(member_id)
            if local_member is not None:
                actions.append(
                    PreparedAction(
                        action=ActionType.DELETE,
                        operation=OperationType.PULL,  # Pull to signify local deletion
                        change=ChangeType.CREATE_LOCAL,  # Create local to signify missing local member
                        member=local_member,
                    )
                )
    return actions


def prepare_sync_actions[MemberType: MemberBase](
    changes: ChangesDict[MemberType],
    force_mode: ForceMode,
    *,
    payload_dir: Path | None = None,
    download: bool = False,
) -> list[PreparedAction[MemberType]]:
    """Prepare actions for syncing to Iru.

    Args:
        changes: A dictionary of changes between local and remote repository members.
        force_mode: The force mode to use when resolving conflicts.
        payload_dir: Where installer binaries live, when download is requested.
        download: Whether the pull half should also ensure each app's installer is present locally.

    Returns:
        A list of prepared actions to be taken to sync to Iru.
    """

    def intent(member: MemberType) -> DownloadResult | None:
        return _pull_download_intent(member, payload_dir, download=download, force=force_mode == ForceMode.PULL)

    actions: list[PreparedAction[MemberType]] = []
    for change_type, members in changes.items():
        match change_type:
            case ChangeType.CREATE_LOCAL:
                actions.extend(
                    PreparedAction(
                        action=ActionType.CREATE,
                        operation=OperationType.PUSH,
                        change=change_type,
                        member=local,
                        other=remote,
                    )
                    for local, remote in members
                    if local is not None
                )
            case ChangeType.UPDATE_LOCAL:
                actions.extend(
                    PreparedAction(
                        action=ActionType.UPDATE,
                        operation=OperationType.PUSH,
                        change=change_type,
                        member=local,
                        other=remote,
                    )
                    for local, remote in members
                    if local is not None
                )
            case ChangeType.CREATE_REMOTE:
                actions.extend(
                    PreparedAction(
                        action=ActionType.CREATE,
                        operation=OperationType.PULL,
                        change=change_type,
                        member=remote,
                        other=local,
                        download=intent(remote),
                    )
                    for local, remote in members
                    if remote is not None
                )
            case ChangeType.UPDATE_REMOTE:
                actions.extend(
                    PreparedAction(
                        action=ActionType.UPDATE,
                        operation=OperationType.PULL,
                        change=change_type,
                        member=remote,
                        other=local,
                        download=intent(remote),
                    )
                    for local, remote in members
                    if remote is not None
                )
            case ChangeType.NONE:
                actions.extend(
                    PreparedAction(
                        action=ActionType.TRANSFER,
                        operation=OperationType.PULL,
                        change=change_type,
                        member=remote,
                        other=local,
                        download=planned,
                    )
                    for local, remote in members
                    if remote is not None
                    and (planned := intent(remote))
                    in {DownloadResult.DOWNLOADED, DownloadResult.MISMATCH_SKIPPED, DownloadResult.MIGRATED}
                )
            case ChangeType.CONFLICT:
                if force_mode == ForceMode.PUSH:
                    actions.extend(
                        PreparedAction(
                            action=ActionType.UPDATE,
                            operation=OperationType.PUSH,
                            change=change_type,
                            member=local,
                            other=remote,
                        )
                        for local, remote in members
                        if local is not None
                    )
                elif force_mode == ForceMode.PULL:
                    actions.extend(
                        PreparedAction(
                            action=ActionType.UPDATE,
                            operation=OperationType.PULL,
                            change=change_type,
                            member=remote,
                            other=local,
                            download=intent(remote),
                        )
                        for local, remote in members
                        if remote is not None
                    )
                else:
                    actions.extend(
                        PreparedAction(
                            action=ActionType.SKIP,
                            operation=OperationType.SKIP,
                            change=change_type,
                            member=local,
                            other=remote,
                        )
                        for local, remote in members
                        if local is not None
                    )
    return actions


# --- Error formatting ---
def _flatten_error_body(value: object) -> str:
    """Reduce a parsed JSON error body to a single readable line."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_flatten_error_body(item) for item in value)
    if isinstance(value, dict):
        parts: list[str] = []
        for key, val in value.items():
            flat = _flatten_error_body(val)
            if not flat:
                continue
            # DRF summary keys carry no useful field name; other keys name the field.
            parts.append(flat if key in {"detail", "non_field_errors"} else f"{key}: {flat}")
        return "; ".join(parts)
    return str(value)


def _http_error_detail(error: Exception, url: str | None = None) -> str:
    """Return a user-facing detail, surfacing the API response body for HTTPErrors.

    A 401 short-circuits to a fixed message: the auth gateway returns an unparsable
    body for invalid tokens, so the status code is the only reliable signal. A
    transport failure (connection reset, DNS, timeout) collapses to a connection
    message naming the tenant.
    """
    response = getattr(error, "response", None)
    if isinstance(error, requests.HTTPError) and response is not None:
        if response.status_code == 401:
            return "Invalid or expired API token"
        message = response.text.strip()
        with contextlib.suppress(ValueError):
            flattened = _flatten_error_body(response.json()).strip()
            if flattened:
                message = flattened
        return message.removesuffix(".")
    if isinstance(error, requests.RequestException) and not isinstance(error, requests.HTTPError):
        return f"Could not connect to {url or 'the tenant'}. Check your network connection and try again."
    return str(error)


# --- Blueprint Reconciliation ---
def _normalize_error_message(response: requests.Response) -> str:
    """Return the API error message, unwrapping a JSON ``{"detail": ...}`` or bare-string body."""
    message = response.text
    with contextlib.suppress(ValueError):
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("detail"), str):
            message = body["detail"]
        elif isinstance(body, str):
            message = body
    return message.removesuffix(".")


def _classify_assign_error(
    blueprint_id: str, node_id: str | None, error: Exception, blueprint_name: str | None = None
) -> BlueprintActionOutcome:
    """Classify an exception raised by BlueprintsResource.assign into a blueprint outcome.

    An HTTP 400 whose body reports the duplicate-assignment marker means the pair is
    already in the desired state and is recorded as ``skipped``.

    Args:
        blueprint_id (str): UUID of the Blueprint that was being assigned, or the
            declared name when resolution failed.
        node_id (str | None): UUID of the Assignment Map node, or None for the root node.
        error (Exception): The exception raised by the assign call.
        blueprint_name (str | None): The declared blueprint name, when one was used.

    Returns:
        BlueprintActionOutcome: The classified outcome for the (blueprint, node) pair.

    """
    response = getattr(error, "response", None)
    if isinstance(error, requests.HTTPError) and response is not None:
        if is_duplicate_assignment(response):
            return BlueprintActionOutcome(
                blueprint_id=blueprint_id, node_id=node_id, status="skipped", blueprint_name=blueprint_name
            )
        return BlueprintActionOutcome(
            blueprint_id=blueprint_id,
            node_id=node_id,
            status="failed",
            status_code=response.status_code,
            error_message=_normalize_error_message(response),
            blueprint_name=blueprint_name,
        )
    error_message = _http_error_detail(error) if isinstance(error, requests.RequestException) else type(error).__name__
    return BlueprintActionOutcome(
        blueprint_id=blueprint_id,
        node_id=node_id,
        status="failed",
        error_message=error_message,
        blueprint_name=blueprint_name,
    )


def _reconcile_blueprints(
    config: ApiConfig, resource: BlueprintsResource, member: MemberBase, library_item_id: str
) -> list[BlueprintActionOutcome]:
    """Issue an idempotent assign call for each declared (blueprint, node) pair.

    Each declared blueprint (a UUID or a name) is resolved to a UUID first; an
    unresolvable name is recorded as a failed outcome rather than raised.

    Args:
        config (ApiConfig): The API configuration, used to resolve blueprint names.
        resource (BlueprintsResource): An opened blueprints API session.
        member (MemberBase): The Library Item whose info.ensure_blueprints to apply.
        library_item_id (str): UUID of the Library Item to assign (the post-create Iru ID).

    Returns:
        list[BlueprintActionOutcome]: One outcome per declared pair.

    """
    outcomes: list[BlueprintActionOutcome] = []
    for assignment in member.info.ensure_blueprints or []:
        try:
            blueprint_id = get_blueprint_id(config, assignment.blueprint)
        except ValueError as error:
            # UUIDs always pass through resolution, so a failed lookup means a name was declared.
            outcomes.append(
                BlueprintActionOutcome(
                    blueprint_id=assignment.blueprint,
                    node_id=assignment.node,
                    status="failed",
                    error_message=str(error),
                    blueprint_name=assignment.blueprint,
                )
            )
            continue
        except (requests.RequestException, ValidationError) as error:
            outcomes.append(_classify_assign_error(assignment.blueprint, assignment.node, error, assignment.blueprint))
            continue
        # Reverse-resolve for display so reports always show the tenant's blueprint name;
        # best-effort, so a UUID whose blueprint is unknown still falls back to the UUID.
        blueprint_name = get_blueprint_name(config, blueprint_id)
        try:
            resource.assign(blueprint_id, library_item_id=library_item_id, node=assignment.node)
        except (requests.RequestException, ValidationError) as error:
            outcomes.append(_classify_assign_error(blueprint_id, assignment.node, error, blueprint_name))
        else:
            outcomes.append(
                BlueprintActionOutcome(
                    blueprint_id=blueprint_id,
                    node_id=assignment.node,
                    status="assigned",
                    blueprint_name=blueprint_name,
                )
            )
    return outcomes


def _resolve_result_with_blueprints(base_result: ResultType, outcomes: list[BlueprintActionOutcome]) -> ResultType:
    """Upgrade a content-op result to PARTIAL when any blueprint outcome failed.

    A content FAILURE is never downgraded; a successful content op with at least one
    failed blueprint outcome becomes PARTIAL; otherwise the base result is preserved.
    """
    if base_result is ResultType.FAILURE:
        return ResultType.FAILURE
    if any(outcome.status == "failed" for outcome in outcomes):
        return ResultType.PARTIAL
    return base_result


def _bucket_result[MemberType: MemberBase](
    results: SyncResults[MemberType], result: ActionResponse[MemberType]
) -> None:
    """Append an action response to the matching SyncResults bucket."""
    match result.result:
        case ResultType.SUCCESS:
            results.success.append(result)
        case ResultType.PARTIAL:
            results.partial.append(result)
        case ResultType.FAILURE:
            results.failure.append(result)
        case ResultType.SKIPPED:
            results.skipped.append(result)


def _reconcile_unchanged[MemberType: MemberBase](
    config: ApiConfig,
    results: SyncResults[MemberType],
    local_repo: Repository[MemberType],
    blueprints: BlueprintsResource,
) -> None:
    """Reconcile blueprints for selected Library Items that produced no content action.

    Args:
        config (ApiConfig): The API configuration, used to resolve blueprint names.
        results (SyncResults): The content-op responses, also appended to here.
        local_repo (Repository): The selected Library Items.
        blueprints (BlueprintsResource): An opened blueprints API session.

    """
    # Created items are re-keyed to their new Iru ID (see update_local_member);
    # response.id covers member-None responses like delete success.
    acted = {
        response.member.id if response.member is not None else response.id
        for response in (*results.success, *results.partial, *results.failure, *results.skipped)
    }
    for member in local_repo.values():
        if member.id in acted or not member.info.ensure_blueprints:
            continue
        outcomes = _reconcile_blueprints(config, blueprints, member, member.id)
        result_type = ResultType.PARTIAL if any(o.status == "failed" for o in outcomes) else ResultType.SUCCESS
        _bucket_result(
            results,
            ActionResponse(
                id=member.id,
                action=ActionType.SKIP,
                operation=OperationType.PUSH,
                result=result_type,
                member=member,
                blueprint_outcomes=outcomes,
            ),
        )


def warn_preview_off_if_declared[MemberType: MemberBase](local_repo: Repository[MemberType]) -> None:
    """Warn once when blueprints are declared but preview mode is off."""
    count = sum(1 for member in local_repo.values() if member.info.ensure_blueprints)
    if count:
        items = "Library Item" if count == 1 else "Library Items"
        body = (
            f"`ensure_blueprints` is declared on {count} {items}, but preview mode is off.\n"
            "Blueprint assignment is an experimental feature which may change.\n"
            "\n"
            "- To apply now: re-run with --preview or set IRUCTL_PREVIEW=1\n"
            "- Until then, these declarations are ignored."
        )
        console.print_warning(
            Panel(body, title="Blueprint preview disabled", title_align="left", border_style="warning", expand=False)
        )


def compute_exit_code[MemberType: MemberBase](results: SyncResults[MemberType]) -> int:
    """Return 1 if any item resolved to FAILURE or PARTIAL, else 0."""
    return 1 if (results.failure or results.partial) else 0


# --- Do Action Functions ---

# Cap on concurrent transfers.
_MAX_JOBS = 4


@contextlib.contextmanager
def _cancel_on_interrupt(executor: ThreadPoolExecutor, reporter: BatchReporter) -> Iterator[None]:
    """Turn a Ctrl-C during a concurrent run into a single-press abort.

    Without this, the executor's context exit joins workers (wait=True), so a Ctrl-C blocks until the
    in-flight transfer finishes. Here the first press cancels the reporter, drops queued work, and aborts.
    """
    try:
        yield
    except KeyboardInterrupt:
        reporter.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise typer.Abort from None


def _push_action[MemberType: MemberBase](
    config: ApiConfig,
    action: PreparedAction[MemberType],
    *,
    payload_dir: Path | None = None,
    reporter: StreamReporter = NULL_REPORTER,
) -> ActionResponse[MemberType]:
    """Perform one push over the network and build its response; no local-repo or blueprint writes.

    Safe to run in a worker thread: opens its own API session and touches no shared state beyond the
    thread-safe reporter. Local-repo update and blueprint reconcile happen in _finalize_push.
    """
    if action.operation not in {OperationType.PUSH, OperationType.SKIP}:
        raise ValueError("The action must be a push operation.")

    uploaded = False
    try:
        match action.action:
            case ActionType.CREATE:
                outcome = action.member.push_remote(
                    config=config, create=True, payload_dir=payload_dir, other=action.other, reporter=reporter
                )
                result, uploaded = outcome.payload, outcome.uploaded
            case ActionType.UPDATE:
                outcome = action.member.push_remote(
                    config=config, create=False, payload_dir=payload_dir, other=action.other, reporter=reporter
                )
                result, uploaded = outcome.payload, outcome.uploaded
            case ActionType.DELETE:
                result = action.member.delete_remote(config=config)
            case ActionType.SKIP:
                result = None
            case ActionType.TRANSFER:
                raise ValueError("A push action cannot be a transfer.")
            case ActionType.INVALID:
                raise ValueError("A push action cannot be invalid.")
    except (
        requests.RequestException,
        PayloadTransferError,
        ValueError,
        InvalidRepositoryMemberError,
    ) as e:
        console.print_error(
            f"Failed to {action.action} item in Iru {action.member.id}. {_http_error_detail(e, config.url)}",
            stderr=False,
        )
        transfer = PayloadTransfer.FAILED if isinstance(e, PayloadTransferError) else PayloadTransfer.NONE
        return ActionResponse(
            id=action.member.id,
            action=action.action,
            operation=action.operation,
            result=ResultType.FAILURE,
            member=action.member,
            transfer=transfer,
        )

    if action.action is ActionType.SKIP:
        skip_reason = "conflicting changes" if action.change is ChangeType.CONFLICT else "remote only changes"
        console.print_warning(f"{action.member.name} ({action.member.id}) skipped due to {skip_reason}", stderr=False)
        return ActionResponse(
            id=action.member.id,
            action=action.action,
            operation=action.operation,
            result=ResultType.SKIPPED,
            member=action.member,
        )

    msg = f"{action.member.name} ({action.member.id}) {action.action.past_tense()} in Iru successfully"
    if result is not None and action.member.id != result.id:
        # Newly created items will have a different ID
        msg += f" with new Iru ID: {result.id}"
    console.print_success(msg)

    return ActionResponse(
        id=action.member.id,
        action=action.action,
        operation=action.operation,
        result=ResultType.SUCCESS,
        member=action.member.from_api_payload(result) if result is not None else None,
        transfer=PayloadTransfer.UPLOADED if uploaded else PayloadTransfer.NONE,
    )


def _finalize_push[MemberType: MemberBase](
    config: ApiConfig,
    local_repo: Repository[MemberType],
    action: PreparedAction[MemberType],
    response: ActionResponse[MemberType],
    *,
    preview: bool,
    blueprints: BlueprintsResource | None,
) -> ActionResponse[MemberType]:
    """Apply a successful push's main-thread side effects: update the local repo and reconcile blueprints.

    Run sequentially on the main thread so the shared local repo and blueprints session are never
    touched concurrently. Failures and skips need neither and pass through unchanged.
    """
    if response.result is not ResultType.SUCCESS:
        return response

    # Most API requests return an updated version so update the local repository with the API response
    if action.action is not ActionType.DELETE:
        update_local_member(local_repo, response)

    # Reconcile declared blueprint assignments for items whose content op succeeded.
    if preview and blueprints is not None and action.action in {ActionType.CREATE, ActionType.UPDATE}:
        # Newly created items receive a new Iru ID; the assign call must target it.
        library_item_id = response.member.id if response.member is not None else response.id
        response.blueprint_outcomes = _reconcile_blueprints(config, blueprints, action.member, library_item_id)
        response.result = _resolve_result_with_blueprints(response.result, response.blueprint_outcomes)

    return response


def do_push[MemberType: MemberBase](
    config: ApiConfig,
    local_repo: Repository[MemberType],
    action: PreparedAction[MemberType],
    *,
    preview: bool = False,
    blueprints: BlueprintsResource | None = None,
    payload_dir: Path | None = None,
    reporter: StreamReporter = NULL_REPORTER,
) -> ActionResponse[MemberType]:
    """Push a single action and apply its local-repo and blueprint side effects (sequential convenience)."""
    response = _push_action(config, action, payload_dir=payload_dir, reporter=reporter)
    return _finalize_push(config, local_repo, action, response, preview=preview, blueprints=blueprints)


def do_pushes[MemberType: MemberBase](
    config: ApiConfig,
    local_repo: Repository[MemberType],
    actions: Iterable[PreparedAction[MemberType]],
    *,
    preview: bool = False,
    payload_dir: Path | None = None,
) -> SyncResults[MemberType]:
    """Push the changes defined by the actions tuples to Iru.

    Args:
        config (ApiConfig): The API configuration to use for the push.
        local_repo (Repository): The selected local Library Items.
        actions (Iterable[PreparedAction]): The actions to take.
        preview (bool): When True, reconcile declared blueprint assignments.

    Returns:
        SyncResults: A dataclass containing the successful and failed actions.

    """
    actions = list(actions)
    push_results = SyncResults[MemberType]()
    if not actions and not preview:
        console.print("Nothing to do.")
        return push_results

    # A single blueprints session is opened once and reused across every item in the run.
    session = BlueprintsResource(config) if preview else contextlib.nullcontext()
    with session as blueprints, batch_progress("Pushing changes to Iru", len(actions)) as bar:
        # Transfers are I/O-bound, so threads parallelize them; each push opens its own API session,
        # so concurrent workers share no state. Their main-thread side effects (local-repo update,
        # blueprint reconcile) are applied here as each completes, and results bucketed in action order.
        responses: list[ActionResponse[MemberType] | None] = [None] * len(actions)
        with ThreadPoolExecutor(max_workers=_MAX_JOBS) as executor:
            futures = {
                executor.submit(_push_action, config, action, payload_dir=payload_dir, reporter=bar): index
                for index, action in enumerate(actions)
            }
            with _cancel_on_interrupt(executor, bar):
                for future in as_completed(futures):
                    index = futures[future]
                    action = actions[index]
                    try:
                        response = _finalize_push(
                            config, local_repo, action, future.result(), preview=preview, blueprints=blueprints
                        )
                    except Exception as e:
                        console.print_error(f"Failed to push {action.member.id}: {e}", stderr=False)
                        console.debug(f"Traceback for failed push {action.member.id}:\n{traceback.format_exc()}")
                        response = ActionResponse(
                            id=action.member.id,
                            action=action.action,
                            operation=action.operation,
                            result=ResultType.FAILURE,
                            member=action.member,
                        )
                    streamed = response.transfer is PayloadTransfer.UPLOADED
                    bar.advance_item((1 - UPLOAD_FRACTION) if streamed else 1.0)
                    responses[index] = response

        for response in responses:
            if response is not None:
                _bucket_result(push_results, response)

        if preview and blueprints is not None:
            _reconcile_unchanged(config, push_results, local_repo, blueprints)

    return push_results


def _mismatch_hint(member_id: str) -> str:
    """The differing-installer warning, pointing at the dedicated app download command for this app."""
    return f"the local installer differs from Iru. Run `{APP_NAME} app download {member_id} --force` to overwrite it."


def _relative_to_cwd(path: Path) -> Path:
    """Show path relative to the working directory when it is under it, else as-is (absolute)."""
    try:
        return path.relative_to(Path.cwd())
    except ValueError:
        return path


def _print_download_success(member: MemberBase, payload_dir: Path | None) -> None:
    """Announce an installer download, naming the binary file rather than the member."""
    name = member.info.file.name if isinstance(member, CustomApp) else member.name
    destination = _relative_to_cwd(payload_dir) if payload_dir is not None else "the payload directory"
    console.print_success(f"{name} downloaded to {destination}")


def _print_migrate_success(member: MemberBase) -> None:
    """Announce a legacy installer renamed to its content-addressed name."""
    if isinstance(member, CustomApp):
        console.print_success(f"Existing installer {member.info.file.payload_name} renamed to {member.info.file.name}")
    else:
        console.print_success(f"Existing installer renamed for {member.name}")


def _installer_lock(
    locks: defaultdict[str, threading.Lock], action: PreparedAction[Any], payload_dir: Path | None
) -> threading.Lock | None:
    """The shared lock for this action's target installer file, or None when no file is involved.

    Apps sharing a content-addressed installer name write to the same path; serializing them lets
    the later one re-plan against the finished file instead of downloading it again.
    """
    if payload_dir is not None and isinstance(action.member, CustomApp):
        return locks[action.member.info.file.name]
    return None


# What each executed download result means for the pull's transfer report.
_TRANSFER_BY_RESULT = {
    DownloadResult.DOWNLOADED: PayloadTransfer.DOWNLOADED,
    DownloadResult.MIGRATED: PayloadTransfer.MIGRATED,
    DownloadResult.UP_TO_DATE: PayloadTransfer.NONE,
    DownloadResult.MISMATCH_SKIPPED: PayloadTransfer.MISMATCH,
}


@dataclass
class _DownloadOutcome:
    """What _pull_action did with an installer: moved it, left a mismatch, or failed."""

    transfer: PayloadTransfer = PayloadTransfer.NONE
    failed: bool = False  # the download raised
    mismatch: bool = False  # a differing local installer was left in place (no --force)


def _pull_action[MemberType: MemberBase](
    action: PreparedAction[MemberType],
    *,
    payload_dir: Path | None = None,
    force: bool = False,
    reporter: StreamReporter = NULL_REPORTER,
    lock: threading.Lock | None = None,
) -> _DownloadOutcome:
    """Perform one pull's installer download (if any) and report its outcome; no local-repo writes.

    Safe to run in a worker thread: it opens its own S3 session and touches no shared state beyond
    the thread-safe reporter. lock serializes actions sharing a target installer file; the later
    holder re-plans and finds the file already in place. The metadata write and local-repo mutation
    happen in _finalize_pull.
    """
    member = action.member
    if action.download is DownloadResult.MISMATCH_SKIPPED:
        return _DownloadOutcome(transfer=PayloadTransfer.MISMATCH, mismatch=True)
    if (
        action.download not in {DownloadResult.DOWNLOADED, DownloadResult.MIGRATED}
        or not isinstance(member, CustomApp)
        or payload_dir is None
    ):
        return _DownloadOutcome()

    replaced_sha = action.other.info.file.sha256 if isinstance(action.other, CustomApp) else None
    try:
        with lock if lock is not None else contextlib.nullcontext():
            if action.download is DownloadResult.DOWNLOADED:
                with reporter.stream(f"Downloading {member.info.file.name}", member.file_size) as transfer:
                    result = member.download_binary(
                        payload_dir, force=force, replaced_sha=replaced_sha, on_progress=transfer.advance
                    )
            else:
                # An instant local rename; no byte stream to report.
                result = member.download_binary(payload_dir, force=force, replaced_sha=replaced_sha)
    except (PayloadTransferError, InvalidRepositoryMemberError, OSError) as error:
        verb = "download" if action.download is DownloadResult.DOWNLOADED else "rename"
        console.print_error(f"Failed to {verb} the installer for {member.name} ({member.id}): {error}", stderr=False)
        return _DownloadOutcome(transfer=PayloadTransfer.FAILED, failed=True)
    return _DownloadOutcome(transfer=_TRANSFER_BY_RESULT[result], mismatch=result is DownloadResult.MISMATCH_SKIPPED)


def _finalize_pull[MemberType: MemberBase](
    local_repo: Repository[MemberType],
    action: PreparedAction[MemberType],
    outcome: _DownloadOutcome,
    *,
    payload_dir: Path | None = None,
    info_format: InfoFormat | None = None,
) -> ActionResponse[MemberType]:
    """Apply a pull's main-thread side effects: write metadata, mutate the local repo, build the response.

    Runs on the main thread so the shared local repo is never mutated concurrently.
    """
    if action.operation not in {OperationType.PULL, OperationType.SKIP}:
        raise ValueError("The action must be a pull operation.")
    if local_repo.root is None:
        raise ValueError("The local_repo must have a root path set.")

    if action.action is ActionType.TRANSFER:
        member = action.member
        local_member = local_repo.get(action.member.id)
        if outcome.mismatch:
            console.print_warning(f"{member.name} ({member.id}): {_mismatch_hint(member.id)}", stderr=False)
            result_type = ResultType.SKIPPED
        elif outcome.failed:
            result_type = ResultType.FAILURE
        else:
            if outcome.transfer is PayloadTransfer.MIGRATED:
                _print_migrate_success(member)
            elif outcome.transfer is PayloadTransfer.DOWNLOADED:
                _print_download_success(member, payload_dir)
            if isinstance(local_member, CustomApp) and outcome.transfer in {
                PayloadTransfer.MIGRATED,
                PayloadTransfer.DOWNLOADED,
            }:
                # Re-serialize so a pre-suffix stored file.name matches the on-disk installer.
                local_member.ensure_paths(repo_path=local_repo.root)
                local_member.write()
            result_type = ResultType.SUCCESS
        return ActionResponse(
            id=member.id,
            action=ActionType.TRANSFER,
            operation=OperationType.PULL,
            result=result_type,
            member=local_member,
            transfer=outcome.transfer,
        )

    match action.action:
        case ActionType.CREATE | ActionType.UPDATE:
            # Merge the API response into the local member (if any) and stamp its sync hash
            existing = local_repo.get(action.member.id)
            local_member = type(action.member).synced(existing, action.member)
            # Set the format only for newly created resources.
            if existing is None and info_format is not None:
                local_member.info.format = info_format
            local_member.ensure_paths(repo_path=local_repo.root)
            local_member.write()
            local_repo[action.member.id] = local_member
        case ActionType.DELETE:
            local_member = local_repo.pop(action.member.id)
            delete_member_directory(local_member)
            local_member = None
        case ActionType.SKIP:
            local_member = action.member
        case ActionType.INVALID:
            raise ValueError("A pull action cannot be invalid.")

    if action.action is ActionType.SKIP:
        skip_reason = "conflicting changes" if action.change is ChangeType.CONFLICT else "local only changes"
        console.print_warning(f"{action.member.name} ({action.member.id}) skipped due to {skip_reason}", stderr=False)
        return ActionResponse(
            id=action.member.id,
            action=action.action,
            operation=action.operation,
            result=ResultType.SKIPPED,
            member=local_member,
        )

    console.print_success(
        f"{action.member.name} ({action.member.id}) {action.action.past_tense()} in local repo successfully"
    )

    # The installer rode along as part of the same item; fold its outcome into the result.
    result_type = ResultType.SUCCESS
    if outcome.mismatch:
        console.print_warning(
            f"{action.member.name} ({action.member.id}): {_mismatch_hint(action.member.id)}", stderr=False
        )
        result_type = ResultType.PARTIAL
    elif outcome.failed:
        result_type = ResultType.PARTIAL
    elif outcome.transfer is PayloadTransfer.DOWNLOADED:
        _print_download_success(action.member, payload_dir)
    elif outcome.transfer is PayloadTransfer.MIGRATED:
        _print_migrate_success(action.member)

    return ActionResponse(
        id=action.member.id,
        action=action.action,
        operation=action.operation,
        result=result_type,
        member=local_member,
        transfer=outcome.transfer,
    )


def reformat_members[MemberType: MemberBase](
    local_repo: Repository[MemberType],
    info_format: InfoFormat,
    *,
    dry_run: bool = False,
    exclude_ids: Iterable[str] = (),
) -> list[MemberType]:
    """Rewrite info files whose on-disk format differs from info_format, returning the affected members.

    A suffix that already maps to info_format (e.g. .yml for yaml) is left alone.
    With dry_run, report the members without writing.
    """
    excluded = set(exclude_ids)
    reformatted: list[MemberType] = []
    for member in local_repo.values():
        old_path = member.info.path
        if member.id in excluded or old_path is None or SUFFIX_MAP.get(old_path.suffix) is info_format:
            continue
        if not dry_run:
            # write() keys off the path suffix; format only keeps the in-memory model consistent.
            member.info.format = info_format
            member.info.path = new_path = old_path.with_name(f"info.{info_format}")
            try:
                member.info.write()
            except Exception:
                # Drop the partial file so the old one stays the single info file.
                new_path.unlink(missing_ok=True)
                raise
            old_path.unlink()
        reformatted.append(member)
    return reformatted


def do_pulls[MemberType: MemberBase](
    local_repo: Repository[MemberType],
    actions: list[PreparedAction[MemberType]],
    *,
    payload_dir: Path | None = None,
    force: bool = False,
    info_format: InfoFormat | None = None,
    announce_empty: bool = True,
) -> SyncResults:
    """Pull the changes defined by the actions tuples from Iru.

    With payload_dir set (download opted in), CREATE/UPDATE actions download their installer inline
    and TRANSFER actions download an in-sync app's installer, both reported on the shared bar.
    announce_empty silences the no-actions message when the caller still has work to do after.
    """

    pull_results = SyncResults()
    if not actions:
        if announce_empty:
            console.print("Nothing to do.")
        return pull_results

    if local_repo.root is None:
        raise ValueError("The local_repo must have a root path set.")

    with batch_progress("Pulling changes from Iru", len(actions)) as bar:
        # Installer downloads run concurrently; the metadata write and local-repo mutation are applied
        # here as each completes, and results are bucketed in action order.
        responses: list[ActionResponse[MemberType] | None] = [None] * len(actions)
        locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        with ThreadPoolExecutor(max_workers=_MAX_JOBS) as executor:
            futures = {
                executor.submit(
                    _pull_action,
                    action,
                    payload_dir=payload_dir,
                    force=force,
                    reporter=bar,
                    lock=_installer_lock(locks, action, payload_dir),
                ): index
                for index, action in enumerate(actions)
            }
            with _cancel_on_interrupt(executor, bar):
                for future in as_completed(futures):
                    index = futures[future]
                    response = _finalize_pull(
                        local_repo, actions[index], future.result(), payload_dir=payload_dir, info_format=info_format
                    )
                    streamed = response.transfer is PayloadTransfer.DOWNLOADED
                    bar.advance_item((1 - UPLOAD_FRACTION) if streamed else 1.0)
                    responses[index] = response

        for response in responses:
            if response is not None:
                _bucket_result(pull_results, response)

    return pull_results


def do_sync[MemberType: MemberBase](
    config: ApiConfig,
    local_repo: Repository[MemberType],
    actions: list[PreparedAction[MemberType]],
    description: str = "Syncing changes with Iru",
    *,
    preview: bool = False,
    payload_dir: Path | None = None,
    force: bool = False,
    info_format: InfoFormat | None = None,
) -> SyncResults[MemberType]:
    """Sync local repository with Iru.

    Args:
        config: The API configuration to use for syncing.
        local_repo: The local repository.
        actions: A list of prepared actions to take to sync.
        description: The progress bar description.
        preview: When True, reconcile declared blueprint assignments on the push half.

    Returns:
        SyncResults: A dataclass containing the successful and failed actions.
    """
    results = SyncResults[MemberType]()

    locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

    def transfer(
        action: PreparedAction[MemberType], lock: threading.Lock | None
    ) -> ActionResponse[MemberType] | _DownloadOutcome | None:
        """Worker: the network half of one action (push request, or pull download); None for a skip."""
        match action.operation:
            case OperationType.PUSH:
                return _push_action(config, action, payload_dir=payload_dir, reporter=bar)
            case OperationType.PULL:
                return _pull_action(action, payload_dir=payload_dir, force=force, reporter=bar, lock=lock)
            case _:  # OperationType.SKIP
                return None

    # A single blueprints session is opened once and reused across every item in the run.
    session = BlueprintsResource(config) if preview else contextlib.nullcontext()
    with session as blueprints, batch_progress(description, len(actions)) as bar:
        responses: list[ActionResponse[MemberType] | None] = [None] * len(actions)
        with ThreadPoolExecutor(max_workers=_MAX_JOBS) as executor:
            futures = {
                executor.submit(transfer, action, _installer_lock(locks, action, payload_dir)): index
                for index, action in enumerate(actions)
            }
            with _cancel_on_interrupt(executor, bar):
                for future in as_completed(futures):
                    index = futures[future]
                    action = actions[index]
                    outcome = future.result()
                    if isinstance(outcome, ActionResponse):  # PUSH
                        response = _finalize_push(
                            config, local_repo, action, outcome, preview=preview, blueprints=blueprints
                        )
                    elif isinstance(outcome, _DownloadOutcome):  # PULL
                        response = _finalize_pull(
                            local_repo, action, outcome, payload_dir=payload_dir, info_format=info_format
                        )
                    else:  # SKIP
                        console.print_warning(
                            f"{action.member.name} ({action.member.id}) skipped due to conflicting changes",
                            stderr=False,
                        )
                        response = ActionResponse(
                            id=action.member.id,
                            action=action.action,
                            operation=action.operation,
                            result=ResultType.SKIPPED,
                            member=action.member,
                        )
                    streamed = response.transfer in {PayloadTransfer.UPLOADED, PayloadTransfer.DOWNLOADED}
                    bar.advance_item((1 - UPLOAD_FRACTION) if streamed else 1.0)
                    responses[index] = response

        for response in responses:
            if response is not None:
                _bucket_result(results, response)

        if preview and blueprints is not None:
            _reconcile_unchanged(config, results, local_repo, blueprints)

    return results


# --- Display Report Functions ---


def show_blueprint_report[MemberType: MemberBase](results: SyncResults[MemberType]) -> None:
    """Show the blueprint assignment outcomes recorded during the run, if any.

    Renders, under a bold Blueprint assignments heading, an Assignment Summary of the
    assigned/failed outcomes and a Skipped Assignment Summary of the already-assigned
    skips. Renders nothing when no blueprint work was performed.
    """
    status_styles = {"assigned": "green", "failed": "red"}

    assignment_table = Table(title="Assignment Summary", title_justify="left", box=box.SIMPLE)
    for column in ("Library Item", "Blueprint", "Node", "Status", "Detail"):
        assignment_table.add_column(column)

    skipped_table = Table(title="Skipped Assignment Summary", title_justify="left", box=box.SIMPLE)
    for column in ("Library Item", "Blueprint", "Node", "Detail"):
        skipped_table.add_column(column)

    for response in (*results.success, *results.partial):
        name = escape(response.member.name if response.member else response.id)
        for outcome in response.blueprint_outcomes:
            if outcome.status == "skipped":
                skipped_table.add_row(name, escape(outcome.blueprint_label), outcome.node_id or "-", "Already Assigned")
                continue
            style = status_styles.get(outcome.status, "")
            detail = ""
            if outcome.status == "failed":
                bracket = f"\\[{outcome.status_code}] " if outcome.status_code else ""
                detail = f"{bracket}{escape(outcome.error_message or '')}".strip()
            assignment_table.add_row(
                name,
                escape(outcome.blueprint_label),
                outcome.node_id or "-",
                f"[{style}]{outcome.status}" if style else outcome.status,
                detail,
            )

    if assignment_table.row_count == 0 and skipped_table.row_count == 0:
        return

    console.print_with_leading_blank("Blueprint assignments", style="bold")
    if assignment_table.row_count > 0:
        console.print(assignment_table, new_line_start=True)
    if skipped_table.row_count > 0:
        console.print(skipped_table, new_line_start=True)


def show_blueprint_dry_run[MemberType: MemberBase](
    local_repo: Repository[MemberType], actions: Iterable[PreparedAction[MemberType]]
) -> bool:
    """List the blueprint assignments a real --preview run would attempt; return whether any were listed."""
    excluded = {action.member.id for action in actions if action.operation in {OperationType.SKIP, OperationType.PULL}}
    pending = [member for member in local_repo.values() if member.id not in excluded and member.info.ensure_blueprints]
    if not pending:
        return False

    for member in pending:
        name = escape(member.name)
        member_id = escape(member.id)
        for assignment in member.info.ensure_blueprints:
            blueprint = escape(assignment.blueprint)
            node = f" (node [yellow]{escape(assignment.node)}[/])" if assignment.node else ""
            console.print(
                f"Would have assigned [yellow]{name}[/] ([yellow]{member_id}[/]) "
                f"to blueprint [yellow]{blueprint}[/]{node}"
            )
    return True


def format_list_table[MemberType: MemberBase](
    changes: ChangesDict[MemberType], local_only: bool, remote_only: bool
) -> Table:
    rows: list[dict[str, str]] = []
    for change_type in changes:
        for local, remote in changes[change_type]:
            row = OrderedDict[str, str]()
            if local and remote and local.name != remote.name:
                row["id"] = local.id
                row["name"] = f"[yellow]{local.name} / {remote.name}[/]"
            elif local:
                row["id"] = local.id
                row["name"] = local.name
            elif remote:
                row["id"] = remote.id
                row["name"] = remote.name
            else:
                raise ValueError("Both local and remote are None")

            if local_only is remote_only is False:
                match change_type:
                    case ChangeType.NONE:
                        row["status"] = "[green]No Pending Changes[/]"
                    case ChangeType.CREATE_REMOTE:
                        row["status"] = "[yellow]New Remote Item[/]"
                    case ChangeType.UPDATE_REMOTE:
                        row["status"] = "[yellow]Updated Remote Item[/]"
                    case ChangeType.CREATE_LOCAL:
                        row["status"] = "[yellow]New Local Item[/]"
                    case ChangeType.UPDATE_LOCAL:
                        row["status"] = "[yellow]Updated Local Item[/]"
                    case ChangeType.CONFLICT:
                        row["status"] = "[red]Conflicting Changes[/]"
            rows.append(row)

    table_width = min(max(console.width - 1, 78), 120)
    table = Table(box=box.SIMPLE, width=table_width)

    # Add Headers
    table.add_column("ID", style="bold italic", width=36)
    table.add_column(
        "Name (Local/Remote)" if any(" / " in row["name"] for row in rows) else "Name", overflow="fold", ratio=1
    )
    if local_only is remote_only is False:
        table.add_column("Status", overflow="fold", ratio=1)

    # Add Rows
    for row in rows:
        table.add_row(*row.values())

    return table


def format_plain_text_list[MemberType: MemberBase](
    changes: ChangesDict[MemberType], format: OutputFormat, local_only: bool, remote_only: bool
) -> str:
    """Format the output for the list command as either a human readable table or a machine readable format."""
    rows = []
    for change_type in changes:
        for local, remote in changes[change_type]:
            if local is not None:
                row = {"id": local.id}
            elif remote is not None:
                row = {"id": remote.id}
            else:
                raise ValueError("Both local and remote are None")
            row |= {
                "local": None if local is None else local.prepare_syntax_dict(syntax=format.to_syntax()),
                "remote": None if remote is None else remote.prepare_syntax_dict(syntax=format.to_syntax()),
            }
            if local_only is remote_only is False:
                row["status"] = str(change_type)
            if format is OutputFormat.PLIST:
                # plistlib does not support None values
                row = {k: v for k, v in row.items() if v is not None}
            rows.append(row)

    match format:
        case OutputFormat.PLIST:
            return plistlib.dumps(rows, fmt=plistlib.FMT_XML, sort_keys=False).decode("utf-8")
        case OutputFormat.JSON:
            return json.dumps(rows, indent=2)
        case OutputFormat.YAML:
            output_str = io.StringIO()
            yaml.dump(rows, output_str)
            return output_str.getvalue()
        case _:
            return render_plain_text(
                format_list_table(changes=changes, local_only=local_only, remote_only=remote_only), new_line_start=True
            )


def _show_transfer_summary[MemberType: MemberBase](
    results: SyncResults[MemberType], rows: list[tuple[PayloadTransfer, str]]
) -> None:
    """Print an Installer Transfer Summary table counting binaries that moved."""
    moved = (*results.success, *results.partial)
    table = Table(title="Installer Transfer Summary", title_justify="left", box=box.SIMPLE)
    table.add_column("Transfer", width=12)
    table.add_column("Count", width=8)
    for transfer, label in rows:
        count = sum(1 for result in moved if result.transfer is transfer)
        if count > 0:
            table.add_row(label, str(count))

    if table.row_count > 0:
        console.print(table, new_line_start=True)


def _count_transfer[MemberType: MemberBase](results: SyncResults[MemberType], transfer: PayloadTransfer) -> int:
    """Count items whose binary ended in the given transfer state, across all buckets."""
    buckets = (results.success, results.partial, results.failure, results.skipped)
    return sum(1 for bucket in buckets for response in bucket if response.transfer is transfer)


def show_pull_report[MemberType: MemberBase](
    pull_results: SyncResults[MemberType], changes: ChangesDict[MemberType], force_pull: bool, allow_delete: bool
) -> None:
    """Show a summary of the pull operation results.

    Args:
        pull_results: The results of the pull operation.
        changes: The changes dictionary used to generate the pull results.
        force_pull: Whether the pull operation was forced.
        allow_delete: Whether the pull operation was allowed to delete members.

    """
    console.print_with_leading_blank("Library Item changes", style="bold")

    update_table = Table(title="Updated Item Summary", title_justify="left", box=box.SIMPLE)
    update_table.add_column("Action", width=12)
    update_table.add_column("Success", width=8)
    update_table.add_column("Partial", width=8)
    update_table.add_column("Failure", width=8)

    success_counter = Counter(result.action for result in pull_results.success)
    failure_counter = Counter(result.action for result in pull_results.failure)

    # Pull does no blueprint work, so Partial is structurally always 0; the column exists only
    # for layout symmetry with the push and sync summary tables.
    for action_type, label in (
        (ActionType.CREATE, "Created"),
        (ActionType.UPDATE, "Updated"),
        (ActionType.DELETE, "Deleted"),
    ):
        if success_counter[action_type] > 0 or failure_counter[action_type] > 0:
            update_table.add_row(label, str(success_counter[action_type]), "0", str(failure_counter[action_type]))

    if update_table.row_count > 0:
        console.print(update_table, new_line_start=True)
    else:
        console.print("No Library Item changes from this pull.")

    _show_transfer_summary(
        pull_results, [(PayloadTransfer.DOWNLOADED, "Downloaded"), (PayloadTransfer.MIGRATED, "Renamed")]
    )

    skip_table = Table(title="Skipped Item Summary", title_justify="left", box=box.SIMPLE)
    skip_table.add_column("Reason", width=23)
    skip_table.add_column("Count", width=8)

    if len(changes[ChangeType.NONE]) > 0:
        skip_table.add_row("Already up to date", str(len(changes[ChangeType.NONE])))
    if not allow_delete and len(changes[ChangeType.CREATE_LOCAL]) > 0:
        skip_table.add_row("Local only item", str(len(changes[ChangeType.CREATE_LOCAL])))
    if not force_pull and len(changes[ChangeType.UPDATE_LOCAL]) > 0:
        skip_table.add_row("Local only updates", str(len(changes[ChangeType.UPDATE_LOCAL])))
    if not force_pull and len(changes[ChangeType.CONFLICT]) > 0:
        skip_table.add_row("Conflicting changes", str(len(changes[ChangeType.CONFLICT])))
    mismatches = _count_transfer(pull_results, PayloadTransfer.MISMATCH)
    if mismatches:
        skip_table.add_row("Installer differs", str(mismatches))
    invalid_count = count_invalid_failures(pull_results)
    if invalid_count:
        skip_table.add_row("Invalid remote item", str(invalid_count))

    if skip_table.row_count > 0:
        console.print(skip_table, new_line_start=True)


def show_push_report[MemberType: MemberBase](
    push_results: SyncResults[MemberType], changes: ChangesDict[MemberType], force_push: bool, allow_delete: bool
) -> None:
    """Show a summary of the push operation results.

    Args:
        push_results (SyncResults): The results of the push operation.
        changes (ChangesDict): The changes dictionary used to generate the push results.
        force_push (bool): Whether the push operation was forced.
        allow_delete (bool): Whether the push operation was allowed to delete members.

    """
    console.print_with_leading_blank("Library Item changes", style="bold")

    update_table = Table(title="Updated Item Summary", title_justify="left", box=box.SIMPLE)
    update_table.add_column("Action", width=12)
    update_table.add_column("Success", width=8)
    update_table.add_column("Partial", width=8)
    update_table.add_column("Failure", width=8)

    success_counter = Counter(result.action for result in push_results.success)
    partial_counter = Counter(result.action for result in push_results.partial)
    failure_counter = Counter(result.action for result in push_results.failure)

    for action_type, label in (
        (ActionType.CREATE, "Created"),
        (ActionType.UPDATE, "Updated"),
        (ActionType.DELETE, "Deleted"),
    ):
        if success_counter[action_type] > 0 or partial_counter[action_type] > 0 or failure_counter[action_type] > 0:
            update_table.add_row(
                label,
                str(success_counter[action_type]),
                str(partial_counter[action_type]),
                str(failure_counter[action_type]),
            )

    if update_table.row_count > 0:
        console.print(update_table, new_line_start=True)
    else:
        console.print("No Library Item changes to push.")

    _show_transfer_summary(push_results, [(PayloadTransfer.UPLOADED, "Uploaded")])

    skip_table = Table(title="Skipped Item Summary", title_justify="left", box=box.SIMPLE)
    skip_table.add_column("Reason", width=27)
    skip_table.add_column("Count", width=8)

    # Content-unchanged items whose blueprint assignment failed land in the partial
    # bucket with a SKIP action; they are a subset of "already up to date" and must be
    # shown as needing a re-target rather than counted as fully reconciled.
    needs_retarget = partial_counter[ActionType.SKIP]
    up_to_date = len(changes[ChangeType.NONE]) - needs_retarget
    if up_to_date > 0:
        skip_table.add_row("Already up to date", str(up_to_date))
    if needs_retarget > 0:
        skip_table.add_row("Needs re-target", str(needs_retarget))
    if not allow_delete and len(changes[ChangeType.CREATE_REMOTE]) > 0:
        skip_table.add_row("Remote only item", str(len(changes[ChangeType.CREATE_REMOTE])))
    if not force_push and len(changes[ChangeType.UPDATE_REMOTE]) > 0:
        skip_table.add_row("Remote only updates", str(len(changes[ChangeType.UPDATE_REMOTE])))
    if not force_push and len(changes[ChangeType.CONFLICT]) > 0:
        skip_table.add_row("Conflicting changes", str(len(changes[ChangeType.CONFLICT])))
    invalid_count = count_invalid_failures(push_results)
    if invalid_count:
        skip_table.add_row("Invalid remote item", str(invalid_count))

    if skip_table.row_count > 0:
        console.print(skip_table, new_line_start=True)


def show_delete_report[MemberType: MemberBase](sync_results: SyncResults[MemberType]):
    """Show a summary of the delete operation."""
    deleted_table = Table(title="Deleted Item Summary", title_justify="left", box=box.SIMPLE)
    deleted_table.add_column("Location", width=12)
    deleted_table.add_column("Success", width=8)
    deleted_table.add_column("Failure", width=8)

    successes = {
        "both": set[str](),
        "local": {r.id for r in sync_results.success if r.operation is OperationType.PULL},
        "remote": {r.id for r in sync_results.success if r.operation is OperationType.PUSH},
    }
    successes["both"] |= successes["local"] & successes["remote"]
    successes["local"] -= successes["both"]
    successes["remote"] -= successes["both"]

    failures = {
        "both": set[str](),
        "local": {r.id for r in sync_results.failure if r.operation is OperationType.PULL},
        "remote": {r.id for r in sync_results.failure if r.operation is OperationType.PUSH},
    }
    failures["both"] |= failures["local"] & failures["remote"]
    failures["local"] -= failures["both"]
    failures["remote"] -= failures["both"]

    if len(successes["both"]) > 0 or len(failures["both"]) > 0:
        deleted_table.add_row(
            "Both",
            f"[green]{len(successes['both'])}",
            f"[red]{len(failures['both'])}",
        )
    if len(successes["local"]) > 0 or len(failures["local"]) > 0:
        deleted_table.add_row(
            "Local",
            f"[green]{len(successes['local'])}",
            f"[red]{len(failures['local'])}",
        )
    if len(successes["remote"]) > 0 or len(failures["remote"]) > 0:
        deleted_table.add_row(
            "Remote",
            f"[green]{len(successes['remote'])}",
            f"[red]{len(failures['remote'])}",
        )

    if deleted_table.row_count == 0:
        console.print("Nothing was selected for deletion.")
    else:
        console.print(deleted_table, new_line_start=True)


def show_sync_report[MemberType: MemberBase](
    sync_results: SyncResults[MemberType], changes: ChangesDict[MemberType], force_mode: ForceMode
):
    """Show a summary of the sync operation results.

    Args:
        sync_results (SyncResults): The results of the sync operation.
        changes (ChangesDict): The changes dictionary used to generate the sync results.
        force_mode (ForceMode): The force mode used to resolve conflicts.

    """
    console.print_with_leading_blank("Library Item changes", style="bold")

    # Build the Push Summary
    pushed_table = Table(title="Pushed Item Summary", title_justify="left", box=box.SIMPLE)
    pushed_table.add_column("Action", width=12)
    pushed_table.add_column("Success", width=8)
    pushed_table.add_column("Partial", width=8)
    pushed_table.add_column("Failure", width=8)

    push_success_counter = Counter(
        result.action for result in (r for r in sync_results.success if r.operation == OperationType.PUSH)
    )
    push_partial_counter = Counter(
        result.action for result in (r for r in sync_results.partial if r.operation == OperationType.PUSH)
    )
    push_failure_counter = Counter(
        result.action for result in (r for r in sync_results.failure if r.operation == OperationType.PUSH)
    )

    for action_type, label in ((ActionType.CREATE, "Created"), (ActionType.UPDATE, "Updated")):
        if (
            push_success_counter[action_type] > 0
            or push_partial_counter[action_type] > 0
            or push_failure_counter[action_type] > 0
        ):
            pushed_table.add_row(
                label,
                str(push_success_counter[action_type]),
                str(push_partial_counter[action_type]),
                str(push_failure_counter[action_type]),
            )

    # Build the Pull Summary. The pull half does no blueprint work, so Partial is always 0;
    # the column exists only for layout symmetry with the Pushed Item Summary.
    pulled_table = Table(title="Pulled Item Summary", title_justify="left", box=box.SIMPLE)
    pulled_table.add_column("Action", width=12)
    pulled_table.add_column("Success", width=8)
    pulled_table.add_column("Partial", width=8)
    pulled_table.add_column("Failure", width=8)

    pull_success_counter = Counter(
        result.action for result in (r for r in sync_results.success if r.operation == OperationType.PULL)
    )
    pull_failure_counter = Counter(
        result.action for result in (r for r in sync_results.failure if r.operation == OperationType.PULL)
    )
    for action_type, label in ((ActionType.CREATE, "Created"), (ActionType.UPDATE, "Updated")):
        if pull_success_counter[action_type] > 0 or pull_failure_counter[action_type] > 0:
            pulled_table.add_row(
                label, str(pull_success_counter[action_type]), "0", str(pull_failure_counter[action_type])
            )

    if pushed_table.row_count == 0 and pulled_table.row_count == 0:
        console.print("No Library Item changes from this sync.")
    else:
        if pushed_table.row_count > 0:
            console.print(pushed_table, new_line_start=True)
        if pulled_table.row_count > 0:
            console.print(pulled_table, new_line_start=True)

    _show_transfer_summary(
        sync_results,
        [
            (PayloadTransfer.UPLOADED, "Uploaded"),
            (PayloadTransfer.DOWNLOADED, "Downloaded"),
            (PayloadTransfer.MIGRATED, "Renamed"),
        ],
    )

    # Show Skipped Item Summary
    skip_table = Table(title="Skipped Item Summary", title_justify="left", box=box.SIMPLE)
    skip_table.add_column("Reason", width=27)
    skip_table.add_column("Count", width=8)

    needs_retarget = Counter(result.action for result in sync_results.partial)[ActionType.SKIP]
    up_to_date = len(changes[ChangeType.NONE]) - needs_retarget
    if up_to_date > 0:
        skip_table.add_row("Already up to date", str(up_to_date))
    if needs_retarget > 0:
        skip_table.add_row("Needs re-target", str(needs_retarget))
    if force_mode == ForceMode.SKIP and len(changes[ChangeType.CONFLICT]) > 0:
        skip_table.add_row("Conflicting changes", str(len(changes[ChangeType.CONFLICT])))
    mismatches = _count_transfer(sync_results, PayloadTransfer.MISMATCH)
    if mismatches:
        skip_table.add_row("Installer differs", str(mismatches))
    invalid_count = count_invalid_failures(sync_results)
    if invalid_count:
        skip_table.add_row("Invalid remote item", str(invalid_count))

    if skip_table.row_count > 0:
        console.print(skip_table, new_line_start=True)


def save_report[MemberType: MemberBase](
    results: SyncResults[MemberType],
    report_path: Path = REPORT_FILE,
    preview: bool = False,
) -> None:
    """Save the sync report to a file.

    Args:
        sync_results (SyncResults): The results of the sync operation.
        output_path (Path): The path to save the report.

    """
    if report_path.exists():
        with report_path.open("r", encoding="utf-8") as file:
            try:
                sync_report = json.load(file)
                if not isinstance(sync_report, list):
                    raise json.JSONDecodeError("Invalid JSON format", "", 0)
            except json.JSONDecodeError:
                new_name = f"{report_path.name}.bkp_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
                console.warning(
                    f"The sync report file is invalid. The file will be backed up to {new_name}, and a new report file will be created."
                )
                shutil.move(
                    report_path,
                    report_path.with_name(new_name),
                )
                sync_report = []
    else:
        sync_report = []

    # Insert a new report entry
    sync_report.insert(0, results.format_report(preview=preview))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(sync_report, file, indent=2)

    console.info(f"Sync report saved to {report_path}")
