import contextlib
import getpass
import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Generator
from importlib.resources import as_file, files
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

from iructl import _constants, _git
from iructl._constants import (
    APP_NAME,
    DEBUG_ENV,
    GIT_ENV,
    INFO_FORMAT_ENV,
    OUTPUT_FORMAT_ENV,
    PAYLOAD_DIR_ENV,
    PREVIEW_ENV,
    ROOT_MARKER,
    TENANT_ENV,
    TOKEN_ENV,
)
from iructl._utils import locate_repo_root
from iructl.api import ApiConfig
from iructl.repository import blueprints as repository_blueprints

_ENV_CLEARED_FOR_ISOLATION = frozenset(
    {DEBUG_ENV, GIT_ENV, INFO_FORMAT_ENV, OUTPUT_FORMAT_ENV, PAYLOAD_DIR_ENV, PREVIEW_ENV}
)
_ENV_OWNED_BY_LOAD_ENV = frozenset({TENANT_ENV, TOKEN_ENV})


# --- Pytest Modifications ---
def pytest_addoption(parser):
    # Add the --run-live option to the pytest command line
    parser.addoption("--run-live", action="store_true", default=False, help="run live tests")


def pytest_collection_modifyitems(config, items):
    # Skip live tests unless the --run-live option is given
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="use --run-live option to run live test")
    for item in items:
        if "allow_http" in item.keywords:
            item.add_marker(skip_live)


def pytest_report_header(config, start_path) -> str:
    """Add the basetemp directory to the report header."""
    return f"basetemp: {tempfile.gettempdir()}/pytest-of-{getpass.getuser()}"


# --- Auto-Use Fixtures ---
@pytest.fixture(autouse=True)
def no_http_requests(monkeypatch, request, response_factory):
    """Disable http requests in tests."""

    if "allow_http" not in request.keywords:

        def urlopen_mock(*args, **kwargs):
            raise RuntimeError("No HTTP requests allowed in unit tests.")

        def fake_get_ping(url, *args, **kwargs) -> requests.Response:
            if url.endswith("/app/v1/ping"):
                return response_factory(200, b'"pong"')
            return requests.get(url, *args, **kwargs)

        monkeypatch.setattr("urllib3.connectionpool.HTTPConnectionPool.urlopen", urlopen_mock)
        monkeypatch.setattr("requests.get", fake_get_ping)
    else:

        class TestSession(requests.sessions.Session):
            """Adds a source=iructl-test param to all requests except S3 uploads."""

            def prepare_request(self, request):
                # Don't add source param to S3 requests (AWS won't accept it)
                if not (request.url and "amazonaws.com" in request.url):
                    # Always iructl-test, never the -ci suffix.
                    request.params |= {"source": "iructl-test"}
                request = super().prepare_request(request)
                return request

        # If the test allows http requests, use a fake session that adds a source=iructl-test param to all requests
        monkeypatch.setattr("requests.sessions.Session", TestSession)
        monkeypatch.setattr("requests.Session", TestSession)


@pytest.fixture(autouse=True, scope="session")
def disable_typer_force_terminal():
    """Render Typer help output as plain text.

    Typer sets rich_utils.FORCE_TERMINAL=True when GITHUB_ACTIONS, FORCE_COLOR, or
    PY_COLORS is in the environment, which injects ANSI escape codes into help output
    and breaks --help text assertions. Disable it two ways:

    - Patch the resolved FORCE_TERMINAL flag for in-process CliRunner tests. Typer
      computes it at import, so setting the env var alone is too late here.
    - Set _TYPER_FORCE_DISABLE_TERMINAL so subprocesses that invoke the CLI inherit
      it and read it at their own import.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("typer.rich_utils.FORCE_TERMINAL", False)
        mp.setenv("_TYPER_FORCE_DISABLE_TERMINAL", "1")
        yield


@pytest.fixture(autouse=True, scope="session")
def disable_forced_terminal_color():
    """Clear color-forcing env vars so Rich never ANSI-colorizes --format json output.

    Otherwise a host/CI that sets these makes json.loads choke on escape codes.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("FORCE_COLOR", raising=False)
        mp.delenv("TTY_COMPATIBLE", raising=False)
        yield


@pytest.fixture(autouse=True, scope="session")
def fixed_terminal_width():
    """Pin the rendered console width so output is deterministic across machines.

    Rich/Typer size output to COLUMNS (or the terminal) when it is unset, so wrapping
    differs between a local terminal and CI. Pin it to a fixed width (matching CI's
    default of 80) so in-process and subprocess CLI output render identically. Content
    assertions still go through normalize_output to stay wrap-independent.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("COLUMNS", "80")
        yield


@pytest.fixture(autouse=True, scope="session")
def git_config_isolation():
    """Isolate git operations from user and system config."""
    gitconfig = as_file(files("tests.resources") / "gitconfig")
    with gitconfig as gitconfig_path, pytest.MonkeyPatch.context() as mp:
        mp.setenv("GIT_CONFIG_SYSTEM", os.devnull)
        mp.setenv("GIT_CONFIG_GLOBAL", str(gitconfig_path))
        yield


@pytest.fixture(autouse=True, scope="session")
def git_dir_isolation():
    """Unset GIT_DIR and GIT_WORK_TREE so tests resolve their own repositories."""
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("GIT_DIR", raising=False)
        mp.delenv("GIT_WORK_TREE", raising=False)
        yield


@pytest.fixture(autouse=True, scope="session")
def user_config_isolation(tmp_path_factory):
    """Isolate the per-user config file and env-driven settings.

    USER_CONFIG_FILE is resolved at import, so two layers are needed (mirroring
    disable_typer_force_terminal):

    - Patch the already-bound constant for in-process CliRunner tests; setting the env var
      alone is too late, the value was computed when iructl._config was imported.
    - Set XDG_CONFIG_HOME so subprocesses that invoke the CLI compute an isolated path at
      their own import.

    Both point at the same empty dir. Tests that need a user layer override USER_CONFIG_FILE
    with their own file. The guard fails if a new IRUCTL_* var in _constants is neither cleared
    here nor owned by load_env, so future settings can't silently leak from the host.
    """
    declared = {
        value
        for name, value in vars(_constants).items()
        if name.endswith("_ENV") and isinstance(value, str) and value.startswith(f"{_constants.ENV_PREFIX}_")
    }
    unrouted = declared - _ENV_CLEARED_FOR_ISOLATION - _ENV_OWNED_BY_LOAD_ENV
    assert not unrouted, (
        f"Unrouted env var(s) {sorted(unrouted)}: add each to _ENV_CLEARED_FOR_ISOLATION or _ENV_OWNED_BY_LOAD_ENV."
    )

    config_home = tmp_path_factory.mktemp("user-config-home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("XDG_CONFIG_HOME", str(config_home))
        mp.setattr("iructl._config.USER_CONFIG_FILE", config_home / APP_NAME / "config.yaml")
        for var in _ENV_CLEARED_FOR_ISOLATION:
            mp.delenv(var, raising=False)
        yield


@pytest.fixture(autouse=True)
def git_locate_git_cache_clear():
    """Clear the cache before each test."""
    _git.locate_git.cache_clear()


@pytest.fixture(autouse=True)
def git_locate_root_cache_clear():
    """Clear the cache before each test."""
    _git.locate_git_root.cache_clear()


@pytest.fixture(autouse=True)
def repository_locate_root_cache_clear():
    """Clear the cache before each test."""
    locate_repo_root.cache_clear()


@pytest.fixture(autouse=True)
def git_has_user_config_cache_clear():
    """Clear the cache before each test."""
    _git.has_git_user_config.cache_clear()


@pytest.fixture(autouse=True)
def blueprints_cache_clear():
    """Clear the cache before each test."""
    repository_blueprints._blueprints.cache_clear()


@pytest.fixture(autouse=True)
def tmp_path_cd(tmp_path: Path):
    """Change working directory to a temporary directory and return the path."""
    with contextlib.chdir(tmp_path):
        yield tmp_path


@pytest.fixture(autouse=True, scope="session")
def load_env(request: pytest.FixtureRequest, response_factory):
    """Load iructl environment variables."""

    if request.config.getoption("--run-live"):
        # override ensures that the environment variables are loaded even if they are already set
        load_dotenv(override=True)
        if TENANT_ENV not in os.environ or TOKEN_ENV not in os.environ:
            raise OSError("Missing required environment variables.")
        yield
    else:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(TENANT_ENV, "https://xxxxxxxx.api.iru.com")
            mp.setenv(TOKEN_ENV, "00000000-0000-0000-0000-000000000000")
            yield


# --- General Use Fixtures ---
@pytest.fixture
def resources():
    """Return the path to the resources directory"""
    with as_file(files("tests.resources")) as resources_dir:
        yield resources_dir


@pytest.fixture(scope="session")
def config(load_env) -> ApiConfig:
    """Return an ApiConfig object populated with environment variables."""
    return ApiConfig(tenant_url=os.environ[TENANT_ENV], api_token=os.environ[TOKEN_ENV])


@pytest.fixture
def git_repo(tmp_path: Path):
    """Initialize a git repository in a temporary directory and return the path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", repo, "init"], check=True, capture_output=True)
    return repo


@pytest.fixture
def git_remote(tmp_path, git_repo: Path):
    """Initialize a git repository with a remote in a temporary directory and return the repo path."""
    repo = git_repo
    remote = tmp_path / "remote"
    subprocess.run(
        ["git", "-C", repo, "commit", "--allow-empty", "-m", "Initial commit"], check=True, capture_output=True
    )

    remote.mkdir()
    subprocess.run(["git", "-C", remote, "init", "--bare"], check=True, capture_output=True)

    subprocess.run(["git", "-C", repo, "remote", "add", "origin", remote], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "push", "-u", "origin", "main"], check=True, capture_output=True)

    return remote


@pytest.fixture
def iructl_repo(tmp_path: Path) -> Path:
    """Return a temporary directory for a repository."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ROOT_MARKER).touch()
    (repo_path / "profiles").mkdir()
    (repo_path / "scripts").mkdir()
    subprocess.run(["git", "-C", repo_path, "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo_path, "add", "--all"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo_path, "commit", "-m", "Initial commit"], check=True, capture_output=True)
    return repo_path


@pytest.fixture
def iructl_repo_cd(iructl_repo: Path) -> Generator[Path]:
    """Change to the repository directory and return the path."""
    with contextlib.chdir(iructl_repo):
        yield iructl_repo


@pytest.fixture
def file_factory(tmp_path: Path) -> Callable[[Path, str], None]:
    """Return a function that creates a file at a given path in a temp directory with the given content."""

    def create_at_path(path: Path, content: str) -> None:
        path = tmp_path / path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as file:
            file.write(content)

    return create_at_path


@pytest.fixture(scope="session")
def response_factory() -> Callable[[int, dict | bytes], requests.Response]:
    """Return a requests.Response object with the given status code and content."""

    def _response(status_code: int, content: dict | bytes) -> requests.Response:
        response = requests.Response()
        response.status_code = status_code
        if isinstance(content, dict) or isinstance(content, list):
            response._content = json.dumps(content).encode("utf-8")
        elif isinstance(content, bytes):
            response._content = content
        else:
            pytest.fail("Invalid response content passed to response_factory.")
        return response

    return _response
