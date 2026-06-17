"""End-to-end precedence for layered configuration, exercised through `profile list`.

The resolution ladder (highest wins) is: CLI flag > env var > repo `.iructl` > user config >
built-in default. Each test pins one rung and asserts the format actually rendered to the output
file (JSON starts with ``[``, YAML with ``-``, plist with ``<?xml``).
"""

import logging
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from iructl import app
from iructl._cli.common import GlobalState
from iructl._constants import DEBUG_ENV, OUTPUT_FORMAT_ENV, PREVIEW_ENV, ROOT_MARKER, TENANT_ENV
from iructl._diff import ChangeType
from iructl.repository import CustomProfile, Repository

runner = CliRunner()


def _format_of(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith("<?xml"):
        return "plist"
    if stripped.startswith("["):
        return "json"
    if stripped.startswith("-"):
        return "yaml"
    return "unknown"


@pytest.fixture
def repo_root(profiles_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The populated repo root, with cwd moved inside it so `.iructl` is the active config."""
    root = profiles_repo.parent
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def list_format(repo_root: Path):
    """Run `profile list --local` writing to a file, and return the rendered format."""

    def _run(*extra_args: str) -> str:
        outfile = repo_root / "out"
        result = runner.invoke(app, ["profile", "list", "--local", "--output", str(outfile), *extra_args])
        assert result.exit_code == 0, result.output
        return _format_of(outfile.read_text())

    return _run


@pytest.mark.parametrize(
    ("user_config", "repo_config", "env", "flag", "expected"),
    [
        pytest.param("json", None, None, None, "json", id="user-config"),
        pytest.param(None, "json", None, None, "json", id="repo-config"),
        pytest.param("yaml", "json", None, None, "json", id="repo-over-user"),
        pytest.param(None, "json", "yaml", None, "yaml", id="env-over-repo"),
        pytest.param(None, None, "yaml", "plist", "plist", id="flag-over-env"),
    ],
)
def test_output_format_precedence(
    repo_root: Path, list_format, monkeypatch, tmp_path, user_config, repo_config, env, flag, expected
):
    if user_config is not None:
        user_file = tmp_path / "user.yaml"
        user_file.write_text(f"output_format: {user_config}\n")
        monkeypatch.setattr("iructl._config.USER_CONFIG_FILE", user_file)
    if repo_config is not None:
        (repo_root / ROOT_MARKER).write_text(f"output_format: {repo_config}\n")
    if env is not None:
        monkeypatch.setenv(OUTPUT_FORMAT_ENV, env)
    extra_args = ("--format", flag) if flag is not None else ()
    assert list_format(*extra_args) == expected


def test_repo_config_sets_info_format(iructl_repo_cd: Path):
    # info_format is projected onto `profile new`'s --format option via the same default_map.
    (iructl_repo_cd / ROOT_MARKER).write_text("info_format: json\n")

    result = runner.invoke(app, ["profile", "new", "--name", "Cfg Profile"])

    assert result.exit_code == 0, result.output
    assert (iructl_repo_cd / "profiles/Cfg Profile/info.json").is_file()


@pytest.mark.usefixtures("patch_profiles_endpoints")
def test_repo_config_sets_info_format_on_pull(iructl_repo_cd: Path, profiles_lrc):
    # Guards the default_map hookup for pull: _format_key matches by the InfoFormat-typed default.
    _, _, changes = profiles_lrc
    create_id = changes[ChangeType.CREATE_REMOTE][0][1].id
    (iructl_repo_cd / ROOT_MARKER).write_text("info_format: json\n")

    result = runner.invoke(app, ["profile", "pull", "--id", str(create_id)])

    assert result.exit_code == 0, result.output
    assert Repository.load_path(model=CustomProfile)[create_id].info.path.name == "info.json"


@pytest.fixture
def captured_level(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Capture the level handed to logging.basicConfig without reconfiguring real logging.

    `debug` resolves in the root callback, but basicConfig is a no-op once logging is configured, so
    the level argument is the only reliable observable. A no-op spy records it (mirrors test_logging).
    """
    captured: dict = {}

    def _spy(**kwargs):
        captured["level"] = kwargs["level"]

    monkeypatch.setattr(logging, "basicConfig", _spy)
    return captured


@pytest.mark.parametrize(
    ("user_config", "repo_config", "env", "flag", "expected"),
    [
        pytest.param(None, None, None, False, logging.INFO, id="default-off"),
        pytest.param("true", None, None, False, logging.DEBUG, id="user-config"),
        pytest.param("true", "false", None, False, logging.INFO, id="repo-over-user"),
        pytest.param(None, "false", "true", False, logging.DEBUG, id="env-true-over-repo"),
        pytest.param(None, "true", "false", False, logging.INFO, id="env-false-over-repo"),
        pytest.param(None, None, "false", True, logging.DEBUG, id="flag-over-env"),
    ],
)
def test_debug_precedence(
    repo_root: Path, captured_level, monkeypatch, tmp_path, user_config, repo_config, env, flag, expected
):
    if user_config is not None:
        user_file = tmp_path / "user.yaml"
        user_file.write_text(f"debug: {user_config}\n")
        monkeypatch.setattr("iructl._config.USER_CONFIG_FILE", user_file)
    if repo_config is not None:
        (repo_root / ROOT_MARKER).write_text(f"debug: {repo_config}\n")
    if env is not None:
        monkeypatch.setenv(DEBUG_ENV, env)
    flag_args = ("--debug",) if flag else ()
    outfile = repo_root / "out"
    # --log - keeps the spied callback off the real log file; the subcommand only needs to run.
    result = runner.invoke(app, ["--log", "-", *flag_args, "profile", "list", "--local", "--output", str(outfile)])
    assert result.exit_code == 0, result.output
    assert captured_level["level"] == expected


@pytest.fixture
def captured_preview(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Capture the preview value resolved in the root callback and handed to GlobalState.

    `preview` resolves in the root callback and lands on `ctx.obj`; spying on GlobalState
    construction records the resolved bool without changing behaviour (mirrors captured_level).
    """
    captured: dict = {}

    def _spy(**kwargs):
        captured["preview"] = kwargs["preview"]
        return GlobalState(**kwargs)

    monkeypatch.setattr("iructl._cli.GlobalState", _spy)
    return captured


@pytest.mark.parametrize(
    ("user_config", "repo_config", "env", "flag", "expected"),
    [
        pytest.param(None, None, None, False, False, id="default-off"),
        pytest.param("true", None, None, False, True, id="user-config"),
        pytest.param("true", "false", None, False, False, id="repo-over-user"),
        pytest.param(None, "false", "true", False, True, id="env-true-over-repo"),
        pytest.param(None, "true", "false", False, False, id="env-false-over-repo"),
        pytest.param(None, None, "false", True, True, id="flag-over-env"),
    ],
)
def test_preview_precedence(
    repo_root: Path, captured_preview, monkeypatch, tmp_path, user_config, repo_config, env, flag, expected
):
    if user_config is not None:
        user_file = tmp_path / "user.yaml"
        user_file.write_text(f"preview: {user_config}\n")
        monkeypatch.setattr("iructl._config.USER_CONFIG_FILE", user_file)
    if repo_config is not None:
        (repo_root / ROOT_MARKER).write_text(f"preview: {repo_config}\n")
    if env is not None:
        monkeypatch.setenv(PREVIEW_ENV, env)
    flag_args = ("--preview",) if flag else ()
    outfile = repo_root / "out"
    result = runner.invoke(app, ["--log", "-", *flag_args, "profile", "list", "--local", "--output", str(outfile)])
    assert result.exit_code == 0, result.output
    assert captured_preview["preview"] is expected


def test_repo_config_sets_tenant_url(repo_root: Path, monkeypatch):
    # The env var would otherwise win (env > config); drop it so the .iructl value is what resolves.
    monkeypatch.delenv(TENANT_ENV, raising=False)
    (repo_root / ROOT_MARKER).write_text("tenant_url: https://configured.api.iru.com\n")

    captured = {}

    def fake_api_config_prompt(tenant_url, api_token):
        captured["tenant_url"] = tenant_url
        raise typer.Exit(0)

    monkeypatch.setattr("iructl._cli.member.defaults.api_config_prompt", fake_api_config_prompt)

    result = runner.invoke(app, ["profile", "list"])

    assert result.exit_code == 0
    assert captured["tenant_url"] == "https://configured.api.iru.com"
