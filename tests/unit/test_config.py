from pathlib import Path
from typing import Annotated

import pytest
import typer
from typer.core import TyperCommand, TyperGroup
from typer.main import get_command

from iructl._config import (
    IructlConfig,
    build_default_map,
    load_repo_config,
    load_user_config,
    merge_configs,
)
from iructl._console import OutputFormat
from iructl._constants import ROOT_MARKER
from iructl.repository import InfoFormat


def _output_format_command() -> TyperCommand:
    """A leaf command whose --format option defaults to an OutputFormat."""
    app = typer.Typer(add_completion=False)

    @app.command()
    def cmd(format: OutputFormat = OutputFormat.TABLE) -> None: ...

    return get_command(app)


def _info_format_command() -> TyperCommand:
    """A leaf command whose --format option defaults to an InfoFormat."""
    app = typer.Typer(add_completion=False)

    @app.command()
    def cmd(format: InfoFormat = InfoFormat.PLIST) -> None: ...

    return get_command(app)


def _info_command_with_deprecated_alias() -> TyperCommand:
    """An info command with canonical --info-format (param 'format') and the deprecated --format alias.

    Mirrors the real ``new`` commands: the canonical option is named ``format`` but
    flagged ``--info-format``, while the hidden deprecated alias is the separate
    ``deprecated_format`` parameter (default ``None``).
    """
    app = typer.Typer(add_completion=False)

    @app.command()
    def cmd(
        format: Annotated[InfoFormat, typer.Option("--info-format")] = InfoFormat.PLIST,
        deprecated_format: Annotated[InfoFormat | None, typer.Option("--format", "-f")] = None,
    ) -> None: ...

    return get_command(app)


def _tenant_command() -> TyperCommand:
    """A leaf command declaring a --tenant-url option."""
    app = typer.Typer(add_completion=False)

    @app.command()
    def cmd(tenant_url: Annotated[str | None, typer.Option("--tenant-url")] = None) -> None: ...

    return get_command(app)


def _payload_dir_command() -> TyperCommand:
    """A leaf command whose --payload-dir option is the `payload_dir` parameter."""
    app = typer.Typer(add_completion=False)

    @app.command()
    def cmd(payload_dir: Annotated[str | None, typer.Option("--payload-dir")] = None) -> None: ...

    return get_command(app)


def _group_with_format_subcommand() -> TyperGroup:
    """A group whose ``list`` subcommand declares an enum-typed --format option."""
    app = typer.Typer(add_completion=False)

    @app.callback()
    def root() -> None: ...

    @app.command(name="list")
    def list_(format: OutputFormat = OutputFormat.TABLE) -> None: ...

    return get_command(app)


def _group_with_bare_subcommand() -> TyperGroup:
    """A group whose ``bare`` subcommand declares no projectable parameters."""
    app = typer.Typer(add_completion=False)

    @app.callback()
    def root() -> None: ...

    @app.command(name="bare")
    def bare() -> None: ...

    return get_command(app)


def _git_command() -> TyperCommand:
    """A leaf command declaring a --git/--no-git option."""
    app = typer.Typer(add_completion=False)

    @app.command()
    def cmd(git: bool = typer.Option(True, "--git/--no-git")) -> None: ...

    return get_command(app)


class TestLoadUserConfig:
    def test_missing_file_is_empty_config(self, monkeypatch, tmp_path):
        monkeypatch.setattr("iructl._config.USER_CONFIG_FILE", tmp_path / "absent.yaml")
        assert load_user_config() == IructlConfig()

    def test_reads_set_fields(self, monkeypatch, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("output_format: json\n")
        monkeypatch.setattr("iructl._config.USER_CONFIG_FILE", config_file)
        assert load_user_config().output_format is OutputFormat.JSON


class TestLoadRepoConfig:
    def test_empty_marker_is_empty_config(self, iructl_repo: Path):
        # An existing empty .iructl must keep working as "no config".
        assert load_repo_config(iructl_repo) == IructlConfig()

    def test_loads_from_given_path_not_cwd(self, iructl_repo: Path):
        # cwd is tmp_path (via the autouse tmp_path_cd fixture); the repo is a child of it.
        (iructl_repo / ROOT_MARKER).write_text("output_format: yaml\n")
        assert load_repo_config(iructl_repo).output_format is OutputFormat.YAML

    def test_loads_payload_dir(self, iructl_repo: Path):
        (iructl_repo / ROOT_MARKER).write_text("payload_dir: /custom/payloads\n")
        assert load_repo_config(iructl_repo).payload_dir == "/custom/payloads"

    @pytest.mark.parametrize("field", ["payload_dir", "tenant_url"])
    @pytest.mark.parametrize("value", ['""', '"   "'], ids=["empty", "whitespace"])
    def test_blank_string_field_is_unset(self, iructl_repo: Path, field: str, value: str):
        # An explicit blank value on a string field means "unset", not a literal "" (which would
        # otherwise slip past the `is not None` projection and resolve to a bad value).
        (iructl_repo / ROOT_MARKER).write_text(f"{field}: {value}\n")
        assert getattr(load_repo_config(iructl_repo), field) is None

    @pytest.mark.parametrize(
        ("marker_content", "expected"),
        [
            pytest.param("debug: true\n", True, id="true"),
            pytest.param("debug: false\n", False, id="false"),
        ],
    )
    def test_loads_debug_bool(self, iructl_repo: Path, marker_content: str, expected: bool):
        # YAML booleans round-trip onto the bool-typed `debug` field.
        (iructl_repo / ROOT_MARKER).write_text(marker_content)
        assert load_repo_config(iructl_repo).debug is expected

    @pytest.mark.parametrize(
        ("marker_content", "expected"),
        [
            pytest.param("preview: true\n", True, id="true"),
            pytest.param("preview: false\n", False, id="false"),
        ],
    )
    def test_loads_preview_bool(self, iructl_repo: Path, marker_content: str, expected: bool):
        # YAML booleans round-trip onto the bool-typed `preview` field.
        (iructl_repo / ROOT_MARKER).write_text(marker_content)
        assert load_repo_config(iructl_repo).preview is expected

    def test_non_repo_path_is_empty_config(self, tmp_path: Path):
        # tmp_path is not inside any repository; loading must not hard-fail.
        assert load_repo_config(tmp_path) == IructlConfig()

    @pytest.mark.parametrize(
        ("marker_content", "match"),
        [
            pytest.param("api_token: super-secret\n", "api_token", id="api-token-forbidden"),
            pytest.param("output_format: bogus\n", "output_format", id="invalid-enum-value"),
            pytest.param("debug: maybe\n", "debug", id="invalid-bool-value"),
            pytest.param("preview: maybe\n", "preview", id="invalid-preview-bool-value"),
            pytest.param("- not\n- a\n- mapping\n", "expected a mapping", id="non-mapping-config"),
            pytest.param("false\n", "expected a mapping", id="falsy-scalar-config"),
            pytest.param("output_format: [unclosed\n  : : :\n", "Invalid", id="malformed-yaml"),
        ],
    )
    def test_rejects_invalid_config(self, iructl_repo: Path, marker_content: str, match: str):
        (iructl_repo / ROOT_MARKER).write_text(marker_content)
        with pytest.raises(typer.BadParameter, match=match):
            load_repo_config(iructl_repo)


class TestMergeConfigs:
    @pytest.mark.parametrize(
        ("user", "repo", "expected"),
        [
            pytest.param(
                IructlConfig(output_format=OutputFormat.JSON),
                IructlConfig(output_format=OutputFormat.YAML),
                IructlConfig(output_format=OutputFormat.YAML),
                id="repo-overrides-user",
            ),
            pytest.param(
                IructlConfig(tenant_url="https://user.example.com"),
                IructlConfig(),
                IructlConfig(tenant_url="https://user.example.com"),
                id="user-falls-through-when-repo-unset",
            ),
            pytest.param(
                IructlConfig(payload_dir="/user/payloads"),
                IructlConfig(payload_dir="/repo/payloads"),
                IructlConfig(payload_dir="/repo/payloads"),
                id="repo-payload-dir-overrides-user",
            ),
            pytest.param(
                IructlConfig(payload_dir="/user/payloads"),
                IructlConfig(),
                IructlConfig(payload_dir="/user/payloads"),
                id="user-payload-dir-falls-through-when-repo-unset",
            ),
            pytest.param(
                IructlConfig(debug=True),
                IructlConfig(debug=False),
                IructlConfig(debug=False),
                id="repo-debug-false-overrides-user-true",
            ),
            pytest.param(
                IructlConfig(preview=True),
                IructlConfig(preview=False),
                IructlConfig(preview=False),
                id="repo-preview-false-overrides-user-true",
            ),
            pytest.param(IructlConfig(), IructlConfig(), IructlConfig(), id="unset-keys-omitted"),
        ],
    )
    def test_merge(self, user: IructlConfig, repo: IructlConfig, expected: IructlConfig):
        assert merge_configs(user, repo) == expected


class TestBuildDefaultMap:
    @pytest.mark.parametrize(
        ("command", "merged", "expected"),
        [
            pytest.param(
                _output_format_command(),
                IructlConfig(output_format=OutputFormat.JSON),
                {"format": OutputFormat.JSON},
                id="output-format-lands-on-output-command",
            ),
            pytest.param(
                _info_format_command(),
                IructlConfig(info_format=InfoFormat.YAML),
                {"format": InfoFormat.YAML},
                id="info-format-lands-on-info-command",
            ),
            pytest.param(
                _info_format_command(),
                IructlConfig(output_format=OutputFormat.JSON),
                {},
                id="output-format-skips-info-command",
            ),
            pytest.param(
                _info_command_with_deprecated_alias(),
                IructlConfig(info_format=InfoFormat.YAML),
                {"format": InfoFormat.YAML},
                id="info-format-lands-on-canonical-not-deprecated-alias",
            ),
            pytest.param(
                _tenant_command(),
                IructlConfig(tenant_url="https://t.example.com"),
                {"tenant_url": "https://t.example.com"},
                id="tenant-url-lands-on-tenant-command",
            ),
            pytest.param(
                _git_command(),
                IructlConfig(git_enabled=False),
                {},
                id="git-enabled-never-projected",
            ),
            pytest.param(
                _payload_dir_command(),
                IructlConfig(payload_dir="/custom/payloads"),
                {"payload_dir": "/custom/payloads"},
                id="payload-dir-lands-on-payload-dir-param",
            ),
            pytest.param(
                _tenant_command(),
                IructlConfig(payload_dir="/custom/payloads"),
                {},
                id="payload-dir-skips-command-without-the-param",
            ),
            pytest.param(
                _group_with_format_subcommand(),
                IructlConfig(output_format=OutputFormat.JSON),
                {"list": {"format": OutputFormat.JSON}},
                id="projects-onto-nested-command-tree",
            ),
            pytest.param(
                _group_with_bare_subcommand(),
                IructlConfig(output_format=OutputFormat.JSON),
                {},
                id="command-without-matching-param-absent",
            ),
        ],
    )
    def test_build(self, command: TyperGroup | TyperCommand, merged: IructlConfig, expected: dict):
        assert build_default_map(command, merged) == expected
