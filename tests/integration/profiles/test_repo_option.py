from pathlib import Path

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl.repository import CustomProfile, Repository

runner = CliRunner()


@pytest.mark.parametrize(
    ("build_argv", "expect_deprecation"),
    [
        pytest.param(lambda repo: ["--repo", repo, "profile", "list", "--local"], False, id="top-level"),
        pytest.param(lambda repo: ["profile", "list", "--local", "--repo", repo], True, id="subcommand"),
    ],
)
def test_repo_option_targets_repo(profiles_repo: Path, build_argv, expect_deprecation: bool):
    # cwd is the (non-repo) tmp_path via the autouse tmp_path_cd fixture, so a successful
    # listing here can only come from --repo, not the current directory.
    repo_root = profiles_repo.parent
    a_profile_id = next(iter(Repository.load_path(model=CustomProfile, path=profiles_repo).keys()))

    result = runner.invoke(app, build_argv(str(repo_root)))

    assert result.exit_code == 0
    assert a_profile_id in result.output
    assert ("deprecated" in result.output) is expect_deprecation


def test_subcommand_repo_is_hidden_from_help():
    result = runner.invoke(app, ["profile", "list", "--help"])

    assert result.exit_code == 0
    assert "--repo" not in result.output
