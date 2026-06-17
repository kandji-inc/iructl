"""Persistent, layered iructl configuration.

Configuration is resolved across layers, highest precedence first:

1. CLI flag
2. Environment variable
3. Repo-level config (the repo's ``.iructl`` marker file)
4. User-level config (a per-user YAML file)
5. Built-in default

This module owns the schema (:class:`IructlConfig`), the file loaders, the
user/repo merge, and the projection of the merged config onto Click's
``default_map``.
"""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import typer
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from ruamel.yaml import YAMLError

from iructl._console import OutputConsole, OutputFormat
from iructl._constants import APP_BRANDING, IS_KST, ROOT_MARKER, USER_CONFIG_FILE
from iructl._utils import locate_repo_root, validation_error_messages, yaml
from iructl.exceptions import InvalidRepositoryError
from iructl.repository import InfoFormat

__all__ = [
    "IructlConfig",
    "build_default_map",
    "load_repo_config",
    "load_user_config",
    "merge_configs",
]

console = OutputConsole(logging.getLogger(__name__))


class _Param(Protocol):
    """The slice of a Click parameter the default-map projection reads."""

    name: str | None
    default: object


class _Command(Protocol):
    """The slice of a Click command the default-map projection reads."""

    params: Sequence[_Param]


class IructlConfig(BaseModel):
    """Persistable, non-secret settings.

    All fields are optional: ``None`` means "unset at this layer", so layers merge
    with "non-None wins". ``extra="forbid"`` rejects unknown keys, surfacing typos.
    """

    model_config = ConfigDict(extra="forbid")

    info_format: InfoFormat | None = None
    output_format: OutputFormat | None = None
    git_enabled: bool | None = None
    tenant_url: str | None = None
    payload_dir: str | None = None
    debug: bool | None = None
    preview: bool | None = None

    @field_validator("payload_dir", "tenant_url", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """Treat an empty or whitespace-only string field as unset, not a literal "" value."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


def _load_file(path: Path) -> dict:
    """Load a YAML config file to a dict, treating missing/empty files as no config."""
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        # An empty or whitespace-only .iructl is valid and means "no config" (back-compat).
        return {}
    try:
        data = yaml.load(text)
    except YAMLError as error:
        raise typer.BadParameter(f"Invalid {APP_BRANDING} configuration in {path}: {error}") from error
    if data is None:
        # A comment-only or `---` document parses to None and means "no config".
        return {}
    if not isinstance(data, dict):
        raise typer.BadParameter(
            f"Invalid {APP_BRANDING} configuration in {path}: expected a mapping of keys to values."
        )
    return data


def _parse(data: dict, *, source: str) -> IructlConfig:
    """Validate a raw config dict, surfacing errors as a clear usage error."""
    try:
        return IructlConfig.model_validate(data)
    except ValidationError as error:
        details = "\n".join(f"  * {message}" for message in validation_error_messages(error))
        msg = f"Invalid {APP_BRANDING} configuration in {source}:\n{details}"
        console.error(msg)
        raise typer.BadParameter(msg) from error


def load_user_config() -> IructlConfig:
    """Load the per-user config file (``USER_CONFIG_FILE``)."""
    if IS_KST:
        return IructlConfig()
    return _parse(_load_file(USER_CONFIG_FILE), source=str(USER_CONFIG_FILE))


def load_repo_config(repo_path: Path) -> IructlConfig:
    """Load repo-level config from the ``.iructl`` of the repo containing ``repo_path``.

    Returns empty config when ``repo_path`` is not inside a repository: config
    loading must never hard-fail a command (e.g. ``--version``) that needs no repo.
    """
    if IS_KST:
        return IructlConfig()
    try:
        root = locate_repo_root(cd_path=repo_path)
    except InvalidRepositoryError:
        return IructlConfig()
    marker = root / ROOT_MARKER
    return _parse(_load_file(marker), source=str(marker))


def merge_configs(user: IructlConfig, repo: IructlConfig) -> IructlConfig:
    """Overlay repo config on user config; repo's set (non-None) fields win."""
    return user.model_copy(update=repo.model_dump(exclude_none=True))


def _format_key(param: _Param) -> str | None:
    """Which merged key backs the ``format`` parameter, by its enum-typed default.

    Output ``--format`` and the info-file ``--info-format`` share the Click
    parameter name ``format`` and are told apart by their default's enum type. The
    deprecated info-file ``--format`` alias is a separate ``deprecated_format``
    parameter that defaults to ``None``, so it never matches here and keeps
    deferring to ``--info-format`` (the two are reconciled by ``resolve_info_format``).
    """
    if isinstance(param.default, InfoFormat):
        return "info_format"
    if isinstance(param.default, OutputFormat):
        return "output_format"
    return None


def build_default_map(command: _Command, merged: IructlConfig) -> dict:
    """Project the merged config onto the Click command tree as a ``default_map``.

    Each subcommand-level key lands only on commands that declare the backing
    parameter. Output ``--format`` and info-file ``--info-format`` share the
    ``format`` parameter name and are disambiguated by the enum type of its
    default (``OutputFormat`` -> ``output_format``; ``InfoFormat`` ->
    ``info_format``); the deprecated info-file ``--format`` alias is a separate
    ``deprecated_format`` parameter and is deliberately left unset so it stays a
    no-op unless passed explicitly.
    """
    default_map: dict = {}
    params = {param.name: param for param in command.params}

    if "tenant_url" in params and merged.tenant_url is not None:
        default_map["tenant_url"] = merged.tenant_url
    if "payload_dir" in params and merged.payload_dir is not None:
        default_map["payload_dir"] = merged.payload_dir
    if (fmt := params.get("format")) is not None and (key := _format_key(fmt)) is not None:
        if (value := getattr(merged, key)) is not None:
            default_map["format"] = value

    subcommands = getattr(command, "commands", None)
    if subcommands:
        for name, sub in subcommands.items():
            if sub_map := build_default_map(sub, merged):
                default_map[name] = sub_map
    return default_map
