import random

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl._constants import APP_NAME

runner = CliRunner()


@pytest.mark.parametrize(
    ("extra_args", "expected_return"), [pytest.param(["--help"], 0, id="--help"), pytest.param([], 2, id="no args")]
)
def test_help(extra_args: list[str], expected_return: int):
    result = runner.invoke(app, ["profile", "show", *extra_args])

    # Check that the command ran successfully or returned a usage error for no
    # args, respectively
    assert result.exit_code == expected_return

    # Check that the help message contains the expected content
    assert f"Usage: {APP_NAME} profile show [OPTIONS] PROFILE" in result.stdout


@pytest.mark.parametrize(
    ("by_id", "pass_remote", "by_parent"),
    [
        pytest.param(True, False, False, id="id-local"),
        pytest.param(False, False, False, id="path-local"),
        pytest.param(False, False, True, id="parent-local"),
        pytest.param(True, True, False, id="id-remote"),
        pytest.param(False, True, False, id="path-remote"),
        pytest.param(False, True, True, id="parent-remote"),
    ],
)
@pytest.mark.usefixtures("iructl_repo_cd")
def test_show_profile(profiles_lrc, patch_profiles_endpoints, by_id: bool, pass_remote: bool, by_parent: bool):
    local, remote, _ = profiles_lrc

    cmd = ["profile", "show"]
    if pass_remote:
        cmd.append("--remote")

    repo = remote if pass_remote else local

    # only use profiles which are in local repo since path could be passed to remote
    profile = random.choice([profile for profile in repo.values() if profile.id in local])

    # get the local profile for profile path option
    local_profile = local[profile.id]
    assert local_profile.profile_path is not None

    cmd.append(
        profile.id
        if by_id
        else str(local_profile.profile_path)
        if by_parent
        else str(local_profile.profile_path.resolve().parent)
    )

    # sanity check for called_dict
    assert all(v == 0 for v in patch_profiles_endpoints.values())

    result = runner.invoke(app, cmd)

    # only git should be called and only if remote is passed
    assert all(v == 0 for k, v in patch_profiles_endpoints.items() if k != "list")
    assert patch_profiles_endpoints["list"] == (1 if pass_remote else 0)

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that the output contains the expected content
    assert profile.id in result.stdout
    assert profile.name in result.stdout


def test_show_from_outside_repo(profiles_lrc):
    local, _, _ = profiles_lrc
    profile = random.choice(list(local.values()))

    result = runner.invoke(app, ["profile", "show", "--repo", str(local.root), profile.id])

    assert result.exit_code == 0

    assert profile.id in result.stdout
    assert profile.name in result.stdout


def test_show_from_outside_repo_with_path(profiles_lrc):
    local, _, _ = profiles_lrc
    profile = random.choice(list(local.values()))

    result = runner.invoke(app, ["profile", "show", str(profile.profile_path)])

    assert result.exit_code == 0

    assert profile.id in result.stdout
    assert profile.name in result.stdout


def test_show_long_name_yaml(iructl_repo_cd):
    # Create a profile with a long name
    long_name = "a" * 10000
    result = runner.invoke(app, ["profile", "new", "--name", long_name])

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that command output contains the expected message
    assert "New profile created at" in result.stdout
    assert long_name[:255] in "".join(result.stdout.splitlines())
    assert long_name[:256] not in "".join(result.stdout.splitlines())

    result = runner.invoke(app, ["profile", "show", "profiles", "--format", "yaml"])
    assert long_name in result.stdout
