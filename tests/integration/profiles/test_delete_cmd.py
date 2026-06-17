import re
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl._constants import APP_NAME
from iructl._diff import ChangeType
from iructl.repository import CustomProfile, Repository
from tests.output import normalize_output

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["profile", "delete", "--help"])
    assert result.exit_code == 0
    assert f"Usage: {APP_NAME} profile delete" in result.stdout
    assert "Delete profiles from your local repository or Iru." in result.stdout
    assert "Made with ❤ by Iru" in result.stdout


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_all_dry_run(profiles_lrc):
    local, _, _ = profiles_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomProfile) == local

    result = runner.invoke(app, ["profile", "delete", "--all", "--dry-run"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" in result.stdout
    assert len(re.findall(r"Would have deleted profile.*in Iru:", result.stdout)) == 9
    assert len(re.findall(r"Would have deleted profile.*locally:", result.stdout)) == 9
    assert "Dry run complete. No changes were made." in result.stdout

    # Check no profiles have changed
    assert Repository.load_path(model=CustomProfile) == local


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_all(profiles_lrc):
    local, _, _ = profiles_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomProfile) == local

    result = runner.invoke(app, ["profile", "delete", "--all", "--force"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" not in result.stdout
    assert "Deleting 10 profiles..." in result.stdout
    assert len(re.findall(r"local repo", result.stdout)) == 9
    assert len(re.findall(r"in Iru", result.stdout)) == 9
    assert "Dry run complete. No changes were made." not in result.stdout
    assert "Delete operation complete!" in result.stdout

    # Check the summary table
    assert re.search(r"Both\s+8\s+0", result.stdout)
    assert re.search(r"Local\s+1\s+0", result.stdout)
    assert re.search(r"Remote\s+1\s+0", result.stdout)

    # Check no profiles have changed
    assert len(Repository.load_path(model=CustomProfile)) == 0


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_by_id_and_path(profiles_lrc):
    pre_local, remote, changes = profiles_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomProfile) == pre_local

    cmd_args = [
        "profile",
        "delete",
        "--force",
        "--id",
        changes[ChangeType.CREATE_REMOTE][0][1].id,
        "--path",
        str(changes[ChangeType.CREATE_LOCAL][0][0].profile_path.parent),
    ]
    profile_ids = list(set(pre_local.keys()) & set(remote.keys()))[:4]
    for idx, profile_id in enumerate(profile_ids):
        if idx % 2 == 1:
            cmd_args.append("--path")
            cmd_args.append(str(pre_local[profile_id].profile_path.parent))
        else:
            cmd_args.append("--id")
            cmd_args.append(profile_id)
    result = runner.invoke(app, cmd_args)
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" not in result.stdout
    assert "Deleting 6 profiles..." in result.stdout
    assert len(re.findall(r"local repo", result.stdout)) == 5
    assert len(re.findall(r"in Iru", result.stdout)) == 5
    assert "Dry run complete. No changes were made." not in result.stdout
    assert "Delete operation complete!" in result.stdout

    # Check the summary table
    assert re.search(r"Both\s+4\s+0", result.stdout)
    assert re.search(r"Local\s+1\s+0", result.stdout)
    assert re.search(r"Remote\s+1\s+0", result.stdout)

    post_local = Repository.load_path(model=CustomProfile)
    deleted_set = {changes[ChangeType.CREATE_REMOTE][0][1].id, changes[ChangeType.CREATE_LOCAL][0][0].id, *profile_ids}
    all_id_set = set(pre_local.keys()) | set(remote.keys())
    for profile_id in all_id_set:
        if profile_id in deleted_set:
            assert profile_id not in post_local
        else:
            assert profile_id in post_local


@pytest.mark.usefixtures("iructl_repo_cd")
def test_local_only(profiles_lrc):
    local, _, _ = profiles_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomProfile) == local

    result = runner.invoke(app, ["profile", "delete", "--all", "--force", "--local"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" not in result.stdout
    assert "Deleting 9 profiles..." in result.stdout
    assert len(re.findall(r"local repo", result.stdout)) == 9
    assert "Dry run complete. No changes were made." not in result.stdout
    assert "Delete operation complete!" in result.stdout

    # Check the summary table
    assert re.search(r"Local\s+9\s+0", result.stdout)

    # Check no profiles have changed
    assert len(Repository.load_path(model=CustomProfile)) == 0


@pytest.mark.usefixtures("iructl_repo_cd")
def test_remote_only(profiles_lrc, patch_profiles_endpoints):
    local, _, _ = profiles_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomProfile) == local

    result = runner.invoke(app, ["profile", "delete", "--all", "--force", "--remote"])
    assert result.exit_code == 0

    # Check that api was called to delete profiles
    assert patch_profiles_endpoints["delete"] == 9

    # Check output
    assert "Running in dry-run mode" not in result.stdout
    assert "Deleting 9 profiles..." in result.stdout
    assert len(re.findall(r"in Iru", result.stdout)) == 9
    assert "local repo" not in result.stdout
    assert "Dry run complete. No changes were made." not in result.stdout
    assert "Delete operation complete!" in result.stdout

    # Check the summary table
    assert re.search(r"Remote\s+9\s+0", result.stdout)

    # Check no local profiles have changed
    assert Repository.load_path(model=CustomProfile) == local


@pytest.mark.usefixtures("patch_profiles_endpoints", "profiles_lrc", "iructl_repo_cd")
def test_invalid_id():
    random_id = str(uuid4())
    result = runner.invoke(app, ["profile", "delete", "--id", random_id])
    assert result.exit_code == 2
    assert "Repository member with ID" in result.stderr
    assert f"{random_id} not found in local or remote" in result.stderr


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd", "profiles_lrc")
def test_invalid_path():
    missing_path = Path("profiles/invalid")
    assert not missing_path.exists()

    result = runner.invoke(app, ["profile", "delete", "--path", str(missing_path)])
    assert result.exit_code == 2
    assert "does not exist." in normalize_output(result.stderr)
