import re
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from iructl._cli import app
from iructl._constants import APP_NAME
from iructl._diff import ChangeType
from iructl.repository import CustomApp, Repository
from tests.fixtures.apps import INSTALLER, app_to_response, make_local_app, place_installer
from tests.output import normalize_output

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["app", "delete", "--help"])
    assert result.exit_code == 0
    assert f"Usage: {APP_NAME} app delete" in result.stdout
    assert "Delete apps from your local repository or Iru." in result.stdout
    assert "Made with ❤ by Iru" in result.stdout


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_all_dry_run(apps_lrc):
    local, _, _ = apps_lrc

    result = runner.invoke(app, ["app", "delete", "--all", "--dry-run"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" in result.stdout
    assert len(re.findall(r"Would have deleted app.*in Iru:", result.stdout)) == 9
    assert len(re.findall(r"Would have deleted app.*locally:", result.stdout)) == 9
    assert "Dry run complete. No changes were made." in result.stdout

    # Check no apps have changed
    assert Repository.load_path(model=CustomApp) == local


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd", "apps_lrc")
def test_all():
    result = runner.invoke(app, ["app", "delete", "--all", "--force"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" not in result.stdout
    assert "Deleting 10 apps..." in result.stdout
    assert len(re.findall(r"local repo", result.stdout)) == 9
    assert len(re.findall(r"in Iru", result.stdout)) == 9
    assert "Dry run complete. No changes were made." not in result.stdout
    assert "Delete operation complete!" in result.stdout

    # Check the summary table
    assert re.search(r"Both\s+8\s+0", result.stdout)
    assert re.search(r"Local\s+1\s+0", result.stdout)
    assert re.search(r"Remote\s+1\s+0", result.stdout)

    # Check the local repo is now empty
    assert len(Repository.load_path(model=CustomApp)) == 0


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_by_id_and_path(apps_lrc):
    pre_local, remote, changes = apps_lrc

    cmd_args = [
        "app",
        "delete",
        "--force",
        "--id",
        changes[ChangeType.CREATE_REMOTE][0][1].id,
        "--path",
        str(changes[ChangeType.CREATE_LOCAL][0][0].info_path.parent),
    ]
    app_ids = list(set(pre_local.keys()) & set(remote.keys()))[:4]
    for idx, app_id in enumerate(app_ids):
        if idx % 2 == 1:
            cmd_args.append("--path")
            cmd_args.append(str(pre_local[app_id].info_path.parent))
        else:
            cmd_args.append("--id")
            cmd_args.append(app_id)
    result = runner.invoke(app, cmd_args)
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" not in result.stdout
    assert "Deleting 6 apps..." in result.stdout
    assert len(re.findall(r"local repo", result.stdout)) == 5
    assert len(re.findall(r"in Iru", result.stdout)) == 5
    assert "Dry run complete. No changes were made." not in result.stdout
    assert "Delete operation complete!" in result.stdout

    # Check the summary table
    assert re.search(r"Both\s+4\s+0", result.stdout)
    assert re.search(r"Local\s+1\s+0", result.stdout)
    assert re.search(r"Remote\s+1\s+0", result.stdout)

    post_local = Repository.load_path(model=CustomApp)
    deleted_set = {changes[ChangeType.CREATE_REMOTE][0][1].id, changes[ChangeType.CREATE_LOCAL][0][0].id, *app_ids}
    all_id_set = set(pre_local.keys()) | set(remote.keys())
    for app_id in all_id_set:
        if app_id in deleted_set:
            assert app_id not in post_local
        else:
            assert app_id in post_local


@pytest.mark.usefixtures("iructl_repo_cd", "apps_lrc")
def test_local_only():
    result = runner.invoke(app, ["app", "delete", "--all", "--force", "--local"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" not in result.stdout
    assert "Deleting 9 apps..." in result.stdout
    assert len(re.findall(r"local repo", result.stdout)) == 9
    assert "Dry run complete. No changes were made." not in result.stdout
    assert "Delete operation complete!" in result.stdout

    # Check the summary table
    assert re.search(r"Local\s+9\s+0", result.stdout)

    # Check the local repo is now empty
    assert len(Repository.load_path(model=CustomApp)) == 0


@pytest.mark.usefixtures("iructl_repo_cd")
def test_remote_only(apps_lrc, patch_apps_endpoints):
    local, _, _ = apps_lrc

    result = runner.invoke(app, ["app", "delete", "--all", "--force", "--remote"])
    assert result.exit_code == 0

    # Check that the api was called to delete the remote apps
    assert patch_apps_endpoints["delete"] == 9

    # Check output
    assert "Running in dry-run mode" not in result.stdout
    assert "Deleting 9 apps..." in result.stdout
    assert len(re.findall(r"in Iru", result.stdout)) == 9
    assert "local repo" not in result.stdout
    assert "Dry run complete. No changes were made." not in result.stdout
    assert "Delete operation complete!" in result.stdout

    # Check the summary table
    assert re.search(r"Remote\s+9\s+0", result.stdout)

    # Check no local apps have changed
    assert Repository.load_path(model=CustomApp) == local


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_installer_binary_survives_delete(iructl_repo_cd, custom_app_factory, apps_remote):
    # Deleting an app removes the member directory but leaves the installer in the payload dir.
    member = make_local_app(iructl_repo_cd, custom_app_factory)
    apps_remote[member.id] = CustomApp.from_api_payload(app_to_response(member))
    installer = place_installer(iructl_repo_cd)

    result = runner.invoke(app, ["app", "delete", "--all", "--force"])

    assert result.exit_code == 0, result.output
    assert len(Repository.load_path(model=CustomApp)) == 0
    assert installer.read_bytes() == INSTALLER


@pytest.mark.usefixtures("patch_apps_endpoints", "apps_lrc", "iructl_repo_cd")
def test_invalid_id():
    random_id = str(uuid4())
    result = runner.invoke(app, ["app", "delete", "--id", random_id])
    assert result.exit_code == 2
    assert "Repository member with ID" in result.stderr
    assert f"{random_id} not found in" in result.stderr


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_invalid_path(apps_lrc):
    missing_path = Path("apps/invalid")
    assert not missing_path.exists()

    result = runner.invoke(app, ["app", "delete", "--path", str(missing_path)])
    assert result.exit_code == 2
    assert "does not exist." in normalize_output(result.stderr)
