import random

import pytest
from typer.testing import CliRunner

from iructl._cli import app
from iructl._constants import APP_NAME
from iructl.repository import InstallEnforcement, InstallType
from tests.fixtures.apps import APP_SCRIPT_CONTENT, INSTALLER_SHA, make_local_app

runner = CliRunner()


@pytest.mark.parametrize(
    ("extra_args", "expected_return"), [pytest.param(["--help"], 0, id="--help"), pytest.param([], 2, id="no args")]
)
def test_help(extra_args: list[str], expected_return: int):
    result = runner.invoke(app, ["app", "show", *extra_args])

    # Check that the command ran successfully or returned a usage error for no args, respectively
    assert result.exit_code == expected_return

    # Check that the help message contains the expected content
    assert f"Usage: {APP_NAME} app show [OPTIONS] APP" in result.stdout


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
def test_show_app(apps_lrc, patch_apps_endpoints, by_id: bool, pass_remote: bool, by_parent: bool):
    local, remote, _ = apps_lrc

    cmd = ["app", "show"]
    if pass_remote:
        cmd.append("--remote")

    repo = remote if pass_remote else local

    # only use apps which are in the local repo since a path could be passed to the remote
    member = random.choice([member for member in repo.values() if member.id in local])

    # get the local app for the path option
    local_app = local[member.id]
    assert local_app.info_path is not None

    cmd.append(
        member.id if by_id else str(local_app.info_path) if by_parent else str(local_app.info_path.resolve().parent)
    )

    # sanity check for the called dict
    assert all(v == 0 for v in patch_apps_endpoints.values())

    result = runner.invoke(app, cmd)

    # only the list endpoint should be called and only if remote is passed
    assert all(v == 0 for k, v in patch_apps_endpoints.items() if k != "list")
    assert patch_apps_endpoints["list"] == (1 if pass_remote else 0)

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that the output contains the expected content
    assert member.id in result.stdout
    assert member.name in result.stdout


def test_show_from_outside_repo(apps_lrc):
    local, _, _ = apps_lrc
    member = random.choice(list(local.values()))

    result = runner.invoke(app, ["app", "show", "--repo", str(local.root), member.id])

    assert result.exit_code == 0
    assert member.id in result.stdout
    assert member.name in result.stdout


def test_show_from_outside_repo_with_path(apps_lrc):
    local, _, _ = apps_lrc
    member = random.choice(list(local.values()))

    result = runner.invoke(app, ["app", "show", str(member.info_path.parent)])

    assert result.exit_code == 0
    assert member.id in result.stdout
    assert member.name in result.stdout


def test_show_script_slot(iructl_repo_cd, custom_app_factory):
    member = custom_app_factory(
        name="With Audit",
        file_name="installer.pkg",
        file_sha256=INSTALLER_SHA,
        install_type=InstallType.PACKAGE,
        install_enforcement=InstallEnforcement.CONTINUOUSLY_ENFORCE,
        has_audit=True,
        has_preinstall=False,
        has_postinstall=False,
    )
    member.ensure_paths(iructl_repo_cd / "apps")
    member.write()

    result = runner.invoke(app, ["app", "show", member.id, "--script", "audit"])

    assert result.exit_code == 0, result.output
    assert APP_SCRIPT_CONTENT.strip() in result.stdout


def test_show_absent_script_slot_is_silent(iructl_repo_cd, custom_app_factory):
    # An app without a preinstall script renders nothing for that slot, without erroring.
    member = make_local_app(iructl_repo_cd, custom_app_factory)

    result = runner.invoke(app, ["app", "show", member.id, "--script", "preinstall"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == ""


def test_show_invalid_script_slot(iructl_repo_cd, custom_app_factory):
    member = make_local_app(iructl_repo_cd, custom_app_factory)

    result = runner.invoke(app, ["app", "show", member.id, "--script", "bogus"])

    assert result.exit_code != 0
